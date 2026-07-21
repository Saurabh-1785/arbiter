"""State-machine engine contracts — P/Coyote-style runtime verification.

Borrows the idea behind Microsoft Research's P language and its production
successor Coyote (used to verify Windows' USB driver stack and multiple
Azure services): model a protocol as communicating state machines with
explicit safety/liveness properties, and check observed behavior against it.

The DSL (Section 6.4) expresses specs as data (dicts/YAML), not code — this
is what makes it "compile a spec" instead of "hand-write a checker."

Invariants are restricted to conjunctive/stable-predicate classes
(Section 3.1) by construction — this is a principled scoping decision,
not a limitation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Protocol

from backend.verifier.events import Event, HLCTimestamp


# ---------------------------------------------------------------------------
# TransitionResult — outcome of processing an event against a spec
# ---------------------------------------------------------------------------

class TransitionResult(Enum):
    """Outcome of feeding an event to the state-machine engine."""
    TRANSITIONED = "transitioned"             # valid transition taken
    NO_MATCHING_TRANSITION = "no_matching_transition"  # violation — no valid transition
    TIMED_OUT = "timed_out"                   # liveness timeout triggered


@dataclass
class TransitionOutcome:
    """Detailed result of a state-machine transition attempt."""

    result: TransitionResult
    resource_id: str
    from_state: str
    to_state: str | None          # None if no transition taken
    agent_id: str
    event: Event
    violation_reason: str | None = None  # set for violations and timeouts

    def to_dict(self) -> dict[str, Any]:
        """Serialize for WebSocket transport."""
        return {
            "result": self.result.value,
            "resource_id": self.resource_id,
            "from_state": self.from_state,
            "to_state": self.to_state,
            "agent_id": self.agent_id,
            "event_id": self.event.event_id,
            "violation_reason": self.violation_reason,
        }


# ---------------------------------------------------------------------------
# MachineInstance — per-resource state tracking (Patch §4: owner tracking)
# ---------------------------------------------------------------------------

@dataclass
class MachineInstance:
    """Tracks the current state of a single resource through the spec.

    Includes current_owner (Patch §4) for double-claim detection and
    entered_state_at_local_count (Patch §3) for per-resource timeout tracking.
    """

    resource_id: str
    state: str                              # current state name
    current_owner: str | None = None        # agent_id that owns this resource
    entered_state_at_local_count: int = 0   # per-resource event count when state was entered
    local_event_count: int = 0              # total per-resource events seen

    def to_dict(self) -> dict[str, Any]:
        """Serialize for WebSocket transport."""
        return {
            "resource_id": self.resource_id,
            "state": self.state,
            "current_owner": self.current_owner,
            "local_event_count": self.local_event_count,
        }


# ---------------------------------------------------------------------------
# Guard function type and EngineContext (Patch §2)
# ---------------------------------------------------------------------------

class EngineContext:
    """Read-only view passed to guards — lets a guard check state owned by
    another component (e.g. ProtectedResource's highest accepted token)
    without that component depending on the state-machine engine.

    This is the bridge between the spec layer and the fencing layer.
    """

    def __init__(self, protected_resource: Any = None, lease_manager: Any = None):
        self.protected_resource = protected_resource
        self.lease_manager = lease_manager


# Guard function signature: takes an Event and EngineContext, returns bool
GuardFn = Callable[[Event, EngineContext], bool]


# ---------------------------------------------------------------------------
# StateMachineEngine protocol — Section 6.5
# ---------------------------------------------------------------------------

class StateMachineEngineProtocol(Protocol):
    """Protocol for the spec-driven state-machine engine (Section 6.5).

    Tracks current state per resource_id. Returns TransitionOutcome
    indicating whether a valid transition was taken, no matching transition
    was found (= violation), or a liveness timeout was triggered.
    """

    def load_spec(self, spec: dict[str, Any]) -> None:
        """Load a state-machine spec (Section 6.4 DSL format)."""
        ...

    def on_event(self, event: Event) -> TransitionOutcome:
        """Process an event against the loaded spec. Returns the outcome."""
        ...

    def get_state(self, resource_id: str) -> MachineInstance | None:
        """Get the current state of a resource."""
        ...

    def get_all_states(self) -> dict[str, MachineInstance]:
        """Get all tracked resource states (for dashboard)."""
        ...
