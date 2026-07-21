"""State-machine engine implementation — P/Coyote-style runtime verification.

Interprets the DSL from Section 6.4, tracking per-resource state with owner
tracking (Patch §4), per-resource event counters (Patch §3), and a guard
registry (Patch §2) for resolving named guards like fencing_token_valid.

This is what makes it "compile a spec" instead of "hand-write a checker" —
the spec is data, the engine interprets it.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from backend.verifier.events import Event, HLCTimestamp
from backend.verifier.spec.state_machine import (
    TransitionResult,
    TransitionOutcome,
    MachineInstance,
    EngineContext,
    GuardFn,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Built-in guard functions (Patch §2: guard registry)
# ---------------------------------------------------------------------------

def fencing_token_valid(event: Event, ctx: EngineContext) -> bool:
    """Guard: check that the event's fencing token is valid.

    # fencing-token check — Kleppmann, "How to do distributed locking," 2016
    A token is valid if it is >= the highest token the protected resource
    has already accepted for this resource_id.
    """
    token = event.payload.get("fencing_token")
    if token is None or event.resource_id is None:
        return False
    if ctx.protected_resource is None:
        return True  # no protected resource to check against
    return token >= ctx.protected_resource.highest_accepted(event.resource_id)


# Guard registry: maps guard names from the DSL to actual functions
GUARDS: dict[str, GuardFn] = {
    "fencing_token_valid": fencing_token_valid,
}


# ---------------------------------------------------------------------------
# Event-kind to DSL event mapping
# ---------------------------------------------------------------------------

# Maps Event.kind values to the DSL "on" trigger names.
# Some kinds map directly; task_claim maps to the spec's "task_claim" trigger.
KIND_TO_TRIGGER: dict[str, str] = {
    "task_claim": "task_claim",
    "task_release": "task_release",
    "handoff_request": "handoff_request",
    "handoff_ack": "handoff_ack",
    "fencing_conflict": "fencing_conflict",
    "state_transition": "start_work",  # state_transition with to_state=InProgress
}


class StateMachineEngineImpl:
    """Concrete implementation of the spec-driven state-machine engine.

    Tracks current state per resource_id via MachineInstance, which includes:
    - current_owner (Patch §4): for double-claim detection
    - entered_state_at_local_count (Patch §3): for per-resource timeout tracking
    - local_event_count: total per-resource events seen

    The engine interprets the DSL spec, checking guards, handling wildcards,
    and enforcing safety/liveness properties.
    """

    def __init__(self, context: EngineContext | None = None):
        """Initialize the engine.

        Args:
            context: EngineContext providing read-only access to other
                    components (ProtectedResource, LeaseManager) for guards.
        """
        self._spec: dict[str, Any] | None = None
        self._instances: dict[str, MachineInstance] = {}  # resource_id -> instance
        self._context = context or EngineContext()
        self._violation_callbacks: list[Callable] = []

    def on_violation(self, callback: Callable) -> None:
        """Register a callback for violation notifications."""
        self._violation_callbacks.append(callback)

    def load_spec(self, spec: dict[str, Any]) -> None:
        """Load a state-machine spec (Section 6.4 DSL format).

        Expected format:
        {
            "name": str,
            "states": list[str],
            "start": str,
            "transitions": list[dict],  # {from, on, to, guard?, after_events?}
            "safety": str,              # human-readable safety property
            "liveness": str,            # human-readable liveness property
        }

        Args:
            spec: The spec dict to load.
        """
        self._spec = spec
        logger.info("Loaded spec: %s (%d states, %d transitions)",
                     spec["name"], len(spec["states"]), len(spec["transitions"]))

    def _get_or_create_instance(self, resource_id: str) -> MachineInstance:
        """Get or create a MachineInstance for a resource."""
        if resource_id not in self._instances:
            start_state = self._spec["start"] if self._spec else "Idle"
            self._instances[resource_id] = MachineInstance(
                resource_id=resource_id,
                state=start_state,
                current_owner=None,
                entered_state_at_local_count=0,
                local_event_count=0,
            )
        return self._instances[resource_id]

    def _resolve_trigger(self, event: Event) -> str:
        """Map an Event to its DSL trigger name."""
        if event.kind == "state_transition":
            to_state = event.payload.get("to_state", "")
            if to_state == "InProgress":
                return "start_work"
            return event.kind
        return KIND_TO_TRIGGER.get(event.kind, event.kind)

    def _check_timeout_transitions(self, instance: MachineInstance) -> TransitionOutcome | None:
        """Check if any timeout transitions should fire.

        Patch §3: Uses per-resource event count, not global bus count.
        """
        if not self._spec:
            return None

        for transition in self._spec.get("transitions", []):
            after_events = transition.get("after_events")
            if after_events is None:
                continue

            from_state = transition["from"]
            if from_state != "*" and from_state != instance.state:
                continue

            events_in_state = instance.local_event_count - instance.entered_state_at_local_count
            if events_in_state >= after_events:
                old_state = instance.state
                new_state = transition["to"]
                instance.state = new_state
                instance.entered_state_at_local_count = instance.local_event_count

                logger.info(
                    "TIMEOUT: %s: %s -> %s (after %d events in state)",
                    instance.resource_id, old_state, new_state, events_in_state,
                )

                return TransitionOutcome(
                    result=TransitionResult.TIMED_OUT,
                    resource_id=instance.resource_id,
                    from_state=old_state,
                    to_state=new_state,
                    agent_id="system",
                    event=Event.create(
                        agent_id="system",
                        transport="stdout",
                        hlc=HLCTimestamp(l=0, c=0),
                        kind="state_transition",
                        resource_id=instance.resource_id,
                        payload={"from_state": old_state, "to_state": new_state},
                    ),
                    violation_reason=f"Liveness timeout: {old_state} exceeded {after_events} events",
                )

        return None

    def on_event(self, event: Event) -> TransitionOutcome:
        """Process an event against the loaded spec.

        Steps:
        1. Get/create the MachineInstance for this resource
        2. Increment per-resource event counter
        3. Check timeout transitions
        4. Resolve the event's DSL trigger
        5. Find matching transitions (checking guards, owner tracking)
        6. Take the transition or flag a violation

        Patch §4: Owner tracking — on task_claim, checks that no other agent
        currently owns the resource. A double-claim by a different agent is
        treated as a violation.

        Args:
            event: The event to process.

        Returns:
            TransitionOutcome indicating what happened.
        """
        if not self._spec:
            raise RuntimeError("No spec loaded")

        resource_id = event.resource_id
        if resource_id is None:
            # Non-resource events are passed through without transition
            return TransitionOutcome(
                result=TransitionResult.TRANSITIONED,
                resource_id="__none__",
                from_state="N/A",
                to_state="N/A",
                agent_id=event.agent_id,
                event=event,
            )

        instance = self._get_or_create_instance(resource_id)
        instance.local_event_count += 1

        # Check timeouts first (Patch §3)
        timeout_result = self._check_timeout_transitions(instance)
        if timeout_result:
            for cb in self._violation_callbacks:
                cb(timeout_result)
            return timeout_result

        # Resolve the trigger
        trigger = self._resolve_trigger(event)

        # Patch §4: Owner tracking — check BEFORE the transition loop
        # so double-claims are caught regardless of whether the spec
        # defines a task_claim transition from the current state.
        if trigger == "task_claim":
            if (instance.current_owner is not None
                    and instance.current_owner != event.agent_id
                    and instance.state not in ("Idle",)):
                # Double-claim by different agent — violation
                violation = TransitionOutcome(
                    result=TransitionResult.NO_MATCHING_TRANSITION,
                    resource_id=resource_id,
                    from_state=instance.state,
                    to_state=None,
                    agent_id=event.agent_id,
                    event=event,
                    violation_reason=(
                        f"Double-claim violation: {event.agent_id} attempted to claim "
                        f"{resource_id} while {instance.current_owner} owns it "
                        f"(state={instance.state})"
                    ),
                )
                logger.warning("VIOLATION: %s", violation.violation_reason)
                for cb in self._violation_callbacks:
                    cb(violation)
                return violation

        # Find matching transitions
        for transition in self._spec.get("transitions", []):
            from_state = transition["from"]
            on_trigger = transition["on"]

            # Check state match (support wildcard "*")
            if from_state != "*" and from_state != instance.state:
                continue

            # Check trigger match
            if on_trigger != trigger:
                continue

            # Check guard (Patch §2)
            guard_name = transition.get("guard")
            if guard_name:
                guard_fn = GUARDS.get(guard_name)
                if guard_fn and not guard_fn(event, self._context):
                    logger.info(
                        "Guard %s FAILED for %s on %s",
                        guard_name, event.agent_id, resource_id,
                    )
                    continue  # Guard failed, try next transition

            # Take the transition
            old_state = instance.state
            new_state = transition["to"]
            instance.state = new_state
            instance.entered_state_at_local_count = instance.local_event_count

            # Update owner tracking
            if trigger == "task_claim":
                instance.current_owner = event.agent_id
            elif trigger == "task_release" or new_state in ("Idle", "Violated"):
                instance.current_owner = None
            elif trigger == "handoff_ack":
                instance.current_owner = event.agent_id  # new owner

            logger.info(
                "Transition: %s: %s -> %s (trigger=%s, agent=%s)",
                resource_id, old_state, new_state, trigger, event.agent_id,
            )

            return TransitionOutcome(
                result=TransitionResult.TRANSITIONED,
                resource_id=resource_id,
                from_state=old_state,
                to_state=new_state,
                agent_id=event.agent_id,
                event=event,
            )

        # No matching transition found — this is a VIOLATION
        # (unless it's a non-matching event kind for the current state,
        #  in which case we can be lenient for events like tool_call/tool_result
        #  that don't have explicit transitions)
        non_spec_triggers = {"tool_call", "tool_result", "state_transition"}
        if trigger in non_spec_triggers and trigger not in [t["on"] for t in self._spec.get("transitions", [])]:
            # Non-spec event — pass through without flagging
            return TransitionOutcome(
                result=TransitionResult.TRANSITIONED,
                resource_id=resource_id,
                from_state=instance.state,
                to_state=instance.state,  # no state change
                agent_id=event.agent_id,
                event=event,
            )

        violation = TransitionOutcome(
            result=TransitionResult.NO_MATCHING_TRANSITION,
            resource_id=resource_id,
            from_state=instance.state,
            to_state=None,
            agent_id=event.agent_id,
            event=event,
            violation_reason=(
                f"No matching transition: state={instance.state}, "
                f"trigger={trigger}, agent={event.agent_id}"
            ),
        )
        logger.warning("VIOLATION: %s", violation.violation_reason)
        for cb in self._violation_callbacks:
            cb(violation)
        return violation

    def get_state(self, resource_id: str) -> MachineInstance | None:
        """Get the current state of a resource."""
        return self._instances.get(resource_id)

    def get_all_states(self) -> dict[str, MachineInstance]:
        """Get all tracked resource states (for dashboard)."""
        return dict(self._instances)

    def reset(self) -> None:
        """Clear all state for re-runnability (Patch §8)."""
        self._instances.clear()
        self._violation_callbacks.clear()
