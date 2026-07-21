"""Fencing-token lease management — Kleppmann, "How to do distributed locking," 2016.

Provides the LeaseBackend protocol (swap-point for a real distributed lease
authority like etcd/Raft/Chubby — Section 9 non-goal), InMemoryLeaseBackend
(the demo implementation), and LeaseManager which delegates to the backend.

Lease TTL is counted in per-resource event count, not wall-clock time — this
is a deliberate scoping decision (Section 6.2) for deterministic, testable
behavior during demos.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from backend.verifier.events import HLCTimestamp


# ---------------------------------------------------------------------------
# FencingLease — the lease data contract (Section 6.2)
# ---------------------------------------------------------------------------

@dataclass
class FencingLease:
    """A fencing lease for a resource, with a strictly increasing token.

    The fencing_token is the key correctness mechanism: any downstream
    protected resource MUST reject writes whose token is lower than the
    highest it has already accepted for that resource_id.
    """

    resource_id: str
    owner_agent_id: str
    fencing_token: int          # strictly increasing per resource_id
    issued_at: HLCTimestamp
    lease_ttl_events: int       # event-count TTL, not wall-clock (Section 6.2)
    events_since_issued: int = 0  # per-resource counter for TTL tracking

    def is_expired(self) -> bool:
        """Check if this lease has expired based on per-resource event count."""
        return self.events_since_issued >= self.lease_ttl_events


# ---------------------------------------------------------------------------
# LeaseBackend protocol — swap-point for Section 9's non-goal
# ---------------------------------------------------------------------------

class LeaseBackend(Protocol):
    """Swap point for Section 9's 'real distributed lease authority' non-goal
    (Raft/etcd/Chubby) — the demo only ever uses InMemoryLeaseBackend.

    A future production deployment would implement this backed by etcd, Consul,
    or a Raft group, without changing LeaseManager's logic.
    """

    def next_token(self, resource_id: str) -> int:
        """Return the next strictly increasing fencing token for this resource."""
        ...

    def get_active(self, resource_id: str) -> FencingLease | None:
        """Return the currently active lease, or None if none/expired."""
        ...

    def set_active(self, resource_id: str, lease: FencingLease | None) -> None:
        """Store or clear the active lease for a resource."""
        ...

    def get_event_count(self, resource_id: str) -> int:
        """Return the per-resource event count for TTL tracking."""
        ...

    def increment_event_count(self, resource_id: str) -> int:
        """Increment and return the per-resource event count."""
        ...


# ---------------------------------------------------------------------------
# InMemoryLeaseBackend — the demo implementation
# ---------------------------------------------------------------------------

class InMemoryLeaseBackend:
    """In-memory lease backend for the demo build. Everything lives in
    process-scoped dicts — no persistence, no distribution.

    Satisfies Section 9's 'no persistent storage' non-goal while keeping
    the LeaseBackend interface ready for a real coordination service.
    """

    def __init__(self) -> None:
        self._tokens: dict[str, int] = {}       # resource_id -> last issued token
        self._active: dict[str, FencingLease] = {}  # resource_id -> active lease
        self._event_counts: dict[str, int] = {}  # resource_id -> per-resource event count

    def next_token(self, resource_id: str) -> int:
        """Return the next strictly increasing fencing token for this resource."""
        current = self._tokens.get(resource_id, 0)
        next_val = current + 1
        self._tokens[resource_id] = next_val
        return next_val

    def get_active(self, resource_id: str) -> FencingLease | None:
        """Return the currently active lease, or None if none exists."""
        return self._active.get(resource_id)

    def set_active(self, resource_id: str, lease: FencingLease | None) -> None:
        """Store or clear the active lease for a resource."""
        if lease is None:
            self._active.pop(resource_id, None)
        else:
            self._active[lease.resource_id] = lease

    def get_event_count(self, resource_id: str) -> int:
        """Return the per-resource event count."""
        return self._event_counts.get(resource_id, 0)

    def increment_event_count(self, resource_id: str) -> int:
        """Increment and return the per-resource event count."""
        count = self._event_counts.get(resource_id, 0) + 1
        self._event_counts[resource_id] = count
        return count


# ---------------------------------------------------------------------------
# LeaseManager protocol — Section 6.5
# ---------------------------------------------------------------------------

class LeaseManagerProtocol(Protocol):
    """Protocol for lease management (Section 6.5).

    acquire() MUST return a token strictly greater than any token
    previously issued for the same resource_id.
    """

    def acquire(self, resource_id: str, agent_id: str, at: HLCTimestamp) -> FencingLease:
        """Acquire a lease on a resource, issuing a new fencing token."""
        ...

    def release(self, resource_id: str, agent_id: str) -> bool:
        """Explicitly release a lease. Returns True if released, False if not owner."""
        ...

    def expire_if_stale(self, resource_id: str) -> FencingLease | None:
        """Expire the lease if its per-resource event-count TTL has been exceeded.
        Returns the expired lease if one was expired, None otherwise."""
        ...

    def get_current_lease(self, resource_id: str) -> FencingLease | None:
        """Return the active lease for a resource, or None."""
        ...

    def tick_resource(self, resource_id: str) -> None:
        """Increment the per-resource event counter (called on each bus event
        for this resource). Updates the lease's events_since_issued counter."""
        ...
