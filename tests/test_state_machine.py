"""Tests for the state-machine engine (Section 10).

Tests cover:
- Happy-path transitions match the spec
- Double-claim without release is flagged
- AwaitingAck that never resolves within after_events escalates
- Fencing conflict drives Violated state
"""

from backend.verifier.events import Event, HLCTimestamp
from backend.verifier.spec.state_machine import (
    TransitionResult, TransitionOutcome, MachineInstance, EngineContext,
)
from backend.verifier.spec.state_machine_engine import StateMachineEngineImpl
from backend.verifier.spec.task_ownership_spec import TASK_OWNERSHIP_SPEC
from backend.verifier.lease.protected_resource_impl import ProtectedResourceImpl


def make_event(
    kind: str,
    agent_id: str = "agent-A",
    resource_id: str = "task-42",
    payload: dict | None = None,
    hlc_l: int = 1000,
    hlc_c: int = 0,
) -> Event:
    """Helper to create test events."""
    return Event.create(
        agent_id=agent_id,
        transport="grpc",
        hlc=HLCTimestamp(l=hlc_l, c=hlc_c),
        kind=kind,
        resource_id=resource_id,
        payload=payload or {},
    )


class TestHappyPath:
    """Happy-path transitions should follow the spec exactly."""

    def test_idle_to_claimed(self):
        """Idle -> Claimed on task_claim with valid fencing token."""
        engine = StateMachineEngineImpl()
        engine.load_spec(TASK_OWNERSHIP_SPEC)

        result = engine.on_event(make_event(
            "task_claim",
            payload={"fencing_token": 1, "op": "write"},
        ))
        assert result.result == TransitionResult.TRANSITIONED
        assert result.from_state == "Idle"
        assert result.to_state == "Claimed"

    def test_claimed_to_inprogress(self):
        """Claimed -> InProgress on start_work (state_transition to InProgress)."""
        engine = StateMachineEngineImpl()
        engine.load_spec(TASK_OWNERSHIP_SPEC)

        # First claim
        engine.on_event(make_event(
            "task_claim",
            payload={"fencing_token": 1, "op": "write"},
        ))

        # Then start work
        result = engine.on_event(make_event(
            "state_transition",
            payload={"from_state": "Claimed", "to_state": "InProgress"},
        ))
        assert result.result == TransitionResult.TRANSITIONED
        assert result.from_state == "Claimed"
        assert result.to_state == "InProgress"

    def test_full_happy_path(self):
        """Full path: Idle -> Claimed -> InProgress -> AwaitingAck -> Acked."""
        engine = StateMachineEngineImpl()
        engine.load_spec(TASK_OWNERSHIP_SPEC)

        # Claim
        r1 = engine.on_event(make_event(
            "task_claim", payload={"fencing_token": 1, "op": "write"},
        ))
        assert r1.to_state == "Claimed"

        # Start work
        r2 = engine.on_event(make_event(
            "state_transition",
            payload={"from_state": "Claimed", "to_state": "InProgress"},
        ))
        assert r2.to_state == "InProgress"

        # Handoff request
        r3 = engine.on_event(make_event(
            "handoff_request", payload={"op": "write"},
        ))
        assert r3.to_state == "AwaitingAck"

        # Handoff ack (from a different agent)
        r4 = engine.on_event(make_event(
            "handoff_ack", agent_id="agent-B", payload={"op": "write"},
        ))
        assert r4.to_state == "Acked"

        # Verify final state
        state = engine.get_state("task-42")
        assert state is not None
        assert state.state == "Acked"

    def test_release_from_claimed(self):
        """Claimed -> Idle on task_release."""
        engine = StateMachineEngineImpl()
        engine.load_spec(TASK_OWNERSHIP_SPEC)

        engine.on_event(make_event(
            "task_claim", payload={"fencing_token": 1, "op": "write"},
        ))

        result = engine.on_event(make_event(
            "task_release", payload={"op": "write"},
        ))
        assert result.to_state == "Idle"


class TestViolations:
    """Violations should be properly detected and flagged."""

    def test_double_claim_without_release_flagged(self):
        """A deliberately malformed sequence (double claim without release)
        is flagged as a violation, not silently accepted.

        Patch §4: This is detected via owner tracking — if the resource
        is already claimed by agent-A, agent-B's claim is a violation.
        """
        engine = StateMachineEngineImpl()
        engine.load_spec(TASK_OWNERSHIP_SPEC)

        # Agent A claims
        r1 = engine.on_event(make_event(
            "task_claim", agent_id="agent-A",
            payload={"fencing_token": 1, "op": "write"},
        ))
        assert r1.result == TransitionResult.TRANSITIONED
        assert r1.to_state == "Claimed"

        # Start work (so we're not in Idle)
        engine.on_event(make_event(
            "state_transition", agent_id="agent-A",
            payload={"from_state": "Claimed", "to_state": "InProgress"},
        ))

        # Agent B tries to claim WITHOUT A releasing — VIOLATION
        r2 = engine.on_event(make_event(
            "task_claim", agent_id="agent-B",
            payload={"fencing_token": 2, "op": "write"},
        ))
        assert r2.result == TransitionResult.NO_MATCHING_TRANSITION
        assert r2.violation_reason is not None
        assert "Double-claim" in r2.violation_reason

    def test_fencing_conflict_drives_violated(self):
        """A fencing_conflict event from any state drives the spec to Violated.

        This is the wildcard transition: {from: "*", on: fencing_conflict, to: Violated}
        """
        engine = StateMachineEngineImpl()
        engine.load_spec(TASK_OWNERSHIP_SPEC)

        # Claim task first
        engine.on_event(make_event(
            "task_claim", payload={"fencing_token": 1, "op": "write"},
        ))

        # Fencing conflict
        result = engine.on_event(make_event(
            "fencing_conflict",
            payload={"rejected_token": 1, "highest_token": 2},
        ))
        assert result.result == TransitionResult.TRANSITIONED
        assert result.to_state == "Violated"

        # Verify state
        state = engine.get_state("task-42")
        assert state.state == "Violated"


class TestTimeouts:
    """Liveness timeout should fire when events exceed after_events threshold."""

    def test_awaiting_ack_timeout_escalates(self):
        """An AwaitingAck that never resolves within after_events escalates.

        Patch §3: Uses per-resource event count, not global.
        """
        engine = StateMachineEngineImpl()
        engine.load_spec(TASK_OWNERSHIP_SPEC)

        # Get to AwaitingAck state
        engine.on_event(make_event(
            "task_claim", payload={"fencing_token": 1, "op": "write"},
        ))
        engine.on_event(make_event(
            "state_transition",
            payload={"from_state": "Claimed", "to_state": "InProgress"},
        ))
        engine.on_event(make_event(
            "handoff_request", payload={"op": "write"},
        ))

        state = engine.get_state("task-42")
        assert state.state == "AwaitingAck"

        # Push 20+ events without an ack (the spec says after_events: 20)
        # Using tool_call events that don't trigger transitions but do
        # increment the per-resource counter
        for i in range(20):
            result = engine.on_event(make_event(
                "tool_call",
                payload={"op": "read", "tool_name": "check", "args": {}},
                hlc_c=i + 10,
            ))

        # One of those events should have triggered the timeout
        state = engine.get_state("task-42")
        assert state.state == "Escalated"


class TestGuards:
    """Guard functions should be properly resolved and checked."""

    def test_fencing_token_valid_guard_passes(self):
        """task_claim with a valid fencing token passes the guard."""
        resource = ProtectedResourceImpl()
        ctx = EngineContext(protected_resource=resource)
        engine = StateMachineEngineImpl(context=ctx)
        engine.load_spec(TASK_OWNERSHIP_SPEC)

        result = engine.on_event(make_event(
            "task_claim", payload={"fencing_token": 1, "op": "write"},
        ))
        assert result.result == TransitionResult.TRANSITIONED

    def test_fencing_token_valid_guard_fails(self):
        """task_claim with a stale fencing token fails the guard.

        The protected resource has already accepted token=5, so token=1
        is stale and the guard rejects it.
        """
        resource = ProtectedResourceImpl()
        resource.write("task-42", fencing_token=5, payload={})
        ctx = EngineContext(protected_resource=resource)
        engine = StateMachineEngineImpl(context=ctx)
        engine.load_spec(TASK_OWNERSHIP_SPEC)

        result = engine.on_event(make_event(
            "task_claim", payload={"fencing_token": 1, "op": "write"},
        ))
        # Guard fails, so no transition matches -> violation
        assert result.result == TransitionResult.NO_MATCHING_TRANSITION


class TestMultipleResources:
    """Engine should track state independently per resource."""

    def test_independent_resource_tracking(self):
        """Different resources have independent state machines."""
        engine = StateMachineEngineImpl()
        engine.load_spec(TASK_OWNERSHIP_SPEC)

        # Claim task-42
        engine.on_event(make_event(
            "task_claim", resource_id="task-42",
            payload={"fencing_token": 1, "op": "write"},
        ))

        # Claim task-99 (independent)
        engine.on_event(make_event(
            "task_claim", resource_id="task-99", agent_id="agent-B",
            payload={"fencing_token": 1, "op": "write"},
        ))

        assert engine.get_state("task-42").state == "Claimed"
        assert engine.get_state("task-99").state == "Claimed"

        # Move task-42 to InProgress
        engine.on_event(make_event(
            "state_transition", resource_id="task-42",
            payload={"from_state": "Claimed", "to_state": "InProgress"},
        ))

        # task-42 moved, task-99 unchanged
        assert engine.get_state("task-42").state == "InProgress"
        assert engine.get_state("task-99").state == "Claimed"

    def test_violation_callback_called(self):
        """Violation callbacks are invoked on violations."""
        engine = StateMachineEngineImpl()
        engine.load_spec(TASK_OWNERSHIP_SPEC)

        violations = []
        engine.on_violation(lambda v: violations.append(v))

        # Create a violation: fencing conflict
        engine.on_event(make_event(
            "task_claim", payload={"fencing_token": 1, "op": "write"},
        ))
        engine.on_event(make_event(
            "fencing_conflict",
            payload={"rejected_token": 1, "highest_token": 2},
        ))

        # The fencing_conflict transition is valid (wildcard -> Violated),
        # so no violation callback for that. But let's trigger a real violation:
        violations.clear()

        # Start fresh
        engine.reset()
        engine.load_spec(TASK_OWNERSHIP_SPEC)
        engine.on_violation(lambda v: violations.append(v))

        engine.on_event(make_event(
            "task_claim", agent_id="agent-A",
            payload={"fencing_token": 1, "op": "write"},
        ))
        engine.on_event(make_event(
            "state_transition", agent_id="agent-A",
            payload={"from_state": "Claimed", "to_state": "InProgress"},
        ))
        # Double claim
        engine.on_event(make_event(
            "task_claim", agent_id="agent-B",
            payload={"fencing_token": 2, "op": "write"},
        ))

        assert len(violations) == 1
        assert "Double-claim" in violations[0].violation_reason
