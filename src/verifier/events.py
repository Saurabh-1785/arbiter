"""Event and HLCTimestamp schemas — the foundational data contracts for ARBITER.

Every component imports from this module. The Event schema is shaped as a
compatible superset of an OpenTelemetry span (trace/span-like event_id, an
attributes-style payload bag, HLC riding as span "baggage") so that wiring
real OTel later is a one-line change, not a rewrite.

Payload contracts per kind:
    task_claim:          {"fencing_token": int, "op": "write"}
    task_release:        {"op": "write"}
    handoff_request:     {"op": "write"}
    handoff_ack:         {"op": "write"}
    tool_call:           {"tool_name": str, "args": dict, "op": "read"|"write"}
    tool_result:         {"tool_name": str, "result": Any, "op": "read"|"write"}
    state_transition:    {"from_state": str, "to_state": str}
    fencing_conflict:    {"rejected_token": int, "highest_token": int}
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Literal


# ---------------------------------------------------------------------------
# HLC Timestamp — Demirbas, Leone, Avva, Madeppa & Kulkarni, 2014
# ---------------------------------------------------------------------------

@dataclass(frozen=True, order=False)
class HLCTimestamp:
    """Hybrid Logical Clock timestamp: a pair (l, c) where l is the
    physical-time component (ms since epoch, monotonic non-decreasing)
    and c is the logical counter (resets to 0 whenever l advances).

    Ordering: (l1, c1) < (l2, c2) iff l1 < l2, or l1 == l2 and c1 < c2.
    """

    l: int  # physical-time component (ms since epoch)
    c: int  # logical counter

    def __lt__(self, other: HLCTimestamp) -> bool:
        if not isinstance(other, HLCTimestamp):
            return NotImplemented
        return (self.l, self.c) < (other.l, other.c)

    def __le__(self, other: HLCTimestamp) -> bool:
        if not isinstance(other, HLCTimestamp):
            return NotImplemented
        return (self.l, self.c) <= (other.l, other.c)

    def __gt__(self, other: HLCTimestamp) -> bool:
        if not isinstance(other, HLCTimestamp):
            return NotImplemented
        return (self.l, self.c) > (other.l, other.c)

    def __ge__(self, other: HLCTimestamp) -> bool:
        if not isinstance(other, HLCTimestamp):
            return NotImplemented
        return (self.l, self.c) >= (other.l, other.c)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, HLCTimestamp):
            return NotImplemented
        return (self.l, self.c) == (other.l, other.c)

    def __hash__(self) -> int:
        return hash((self.l, self.c))

    def to_dict(self) -> dict[str, int]:
        """Serialize for JSON/WebSocket transport."""
        return {"l": self.l, "c": self.c}

    @classmethod
    def from_dict(cls, d: dict[str, int]) -> HLCTimestamp:
        """Deserialize from JSON/WebSocket transport."""
        return cls(l=d["l"], c=d["c"])

    def __repr__(self) -> str:
        return f"HLC({self.l}, {self.c})"


# ---------------------------------------------------------------------------
# Transport and Event Kind type aliases
# ---------------------------------------------------------------------------

TransportType = Literal["grpc", "queue", "blackboard", "webhook", "stdout"]

EventKind = Literal[
    "task_claim",
    "task_release",
    "handoff_request",
    "handoff_ack",
    "tool_call",
    "tool_result",
    "state_transition",
    "fencing_conflict",  # Emitted by ProtectedResource on rejection
]


# ---------------------------------------------------------------------------
# Event — the universal event envelope
# ---------------------------------------------------------------------------

@dataclass
class Event:
    """A single coordination event from any agent across any transport.

    The schema is a compatible superset of an OpenTelemetry span: event_id
    maps to a span/trace ID, payload is an attributes-style bag, and the
    HLC timestamp rides as span "baggage".
    """

    event_id: str                          # uuid4
    agent_id: str                          # which agent emitted this
    transport: TransportType               # which transport carried this
    hlc: HLCTimestamp                      # causal timestamp
    kind: EventKind                        # event type
    resource_id: str | None                # which task/lock, if any
    payload: dict[str, Any]                # kind-specific data (see module docstring)
    capability_token: str | None = None    # token for tool-call gating

    @staticmethod
    def create(
        agent_id: str,
        transport: TransportType,
        hlc: HLCTimestamp,
        kind: EventKind,
        resource_id: str | None = None,
        payload: dict[str, Any] | None = None,
        capability_token: str | None = None,
    ) -> Event:
        """Factory with auto-generated event_id."""
        return Event(
            event_id=str(uuid.uuid4()),
            agent_id=agent_id,
            transport=transport,
            hlc=hlc,
            kind=kind,
            resource_id=resource_id,
            payload=payload or {},
            capability_token=capability_token,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSON/WebSocket transport."""
        return {
            "event_id": self.event_id,
            "agent_id": self.agent_id,
            "transport": self.transport,
            "hlc": self.hlc.to_dict(),
            "kind": self.kind,
            "resource_id": self.resource_id,
            "payload": self.payload,
            "capability_token": self.capability_token,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Event:
        """Deserialize from JSON/WebSocket transport."""
        return cls(
            event_id=d["event_id"],
            agent_id=d["agent_id"],
            transport=d["transport"],
            hlc=HLCTimestamp.from_dict(d["hlc"]),
            kind=d["kind"],
            resource_id=d.get("resource_id"),
            payload=d.get("payload", {}),
            capability_token=d.get("capability_token"),
        )
