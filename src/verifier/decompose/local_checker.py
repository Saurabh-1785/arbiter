"""Predicate slicing boundary — Garg & Mittal, ICDCS 2001.

Section 3.7: Build the interface boundary even if the hackathon build only
ever runs one verifier process — that's what turns "how would this scale"
into a strong answer instead of a hand-wave.

In this single-process demo, LocalChecker just wraps the existing global
engine; the point is that a future edge-deployed checker only needs to
implement this interface, not talk to the central verifier for
locally-decidable checks.
"""

from __future__ import annotations

from typing import Any, Protocol

from src.verifier.events import Event
from src.verifier.spec.state_machine_engine import StateMachineEngineImpl


class LocalChecker(Protocol):
    """Predicate-slicing boundary interface.

    A future distributed deployment would run one LocalChecker per agent-pair
    or per-node, checking locally-decidable predicates at the edge and only
    sending compact digests to the central verifier for genuinely cross-cutting
    checks — Garg & Mittal's decomposition, ICDCS 2001.
    """

    def check_local(self, event: Event) -> bool:
        """Check local predicates for this event.

        Returns True if all local invariants pass, False if a local
        violation is detected.
        """
        ...

    def emit_digest(self) -> dict[str, Any]:
        """Emit a compact digest for the central verifier.

        Contains just enough information for the central verifier to check
        cross-cutting predicates — not the full event stream.
        """
        ...


class PassthroughLocalChecker:
    """Demo implementation — always defers to the central engine.

    In this single-process build, there are no local-only invariants
    to check. The point of this class is to prove the interface boundary
    exists — a future edge-deployed checker would implement real logic here.
    """

    def __init__(self, engine: StateMachineEngineImpl):
        self._engine = engine
        self._events_seen: int = 0
        self._resource_ids_seen: set[str] = set()

    def check_local(self, event: Event) -> bool:
        """No local-only invariants defined at this scale — always passes."""
        self._events_seen += 1
        if event.resource_id:
            self._resource_ids_seen.add(event.resource_id)
        return True

    def emit_digest(self) -> dict[str, Any]:
        """Emit a digest with summary statistics for the central verifier."""
        return {
            "events_seen": self._events_seen,
            "resource_ids_seen": list(self._resource_ids_seen),
            "all_states": {
                rid: inst.to_dict()
                for rid, inst in self._engine.get_all_states().items()
            },
        }

    def reset(self) -> None:
        """Clear state for re-runnability."""
        self._events_seen = 0
        self._resource_ids_seen.clear()
