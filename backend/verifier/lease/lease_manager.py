"""LeaseManager implementation — fencing-token lease management.

Implements the LeaseManager using the LeaseBackend protocol for token
persistence. Uses per-resource event counting for TTL (Patch §3).
"""

from __future__ import annotations

import logging
from typing import Any

from backend.verifier.events import HLCTimestamp
from backend.verifier.lease.fencing import (
    FencingLease,
    InMemoryLeaseBackend,
    LeaseBackend,
)

logger = logging.getLogger(__name__)

# Default event-count TTL for leases
DEFAULT_LEASE_TTL_EVENTS = 10


class LeaseManager:
    """Fencing-token lease manager — Kleppmann, "How to do distributed locking," 2016.

    Manages leases for resources with strictly increasing fencing tokens.
    A lease expires after a per-resource event count (not wall-clock time)
    to ensure deterministic, testable behavior during demos.

    Backed by the LeaseBackend protocol — the demo uses InMemoryLeaseBackend,
    but a production deployment could swap in etcd/Raft/Chubby without
    changing this class's logic (Section 9 non-goal).
    """

    def __init__(
        self,
        backend: LeaseBackend | None = None,
        default_ttl_events: int = DEFAULT_LEASE_TTL_EVENTS,
    ):
        """Initialize the lease manager.

        Args:
            backend: The storage backend. Defaults to InMemoryLeaseBackend.
            default_ttl_events: Default lease TTL in per-resource event count.
        """
        self._backend = backend or InMemoryLeaseBackend()
        self._default_ttl_events = default_ttl_events

    def acquire(
        self,
        resource_id: str,
        agent_id: str,
        at: HLCTimestamp,
        ttl_events: int | None = None,
    ) -> FencingLease:
        """Acquire a lease on a resource, issuing a new fencing token.

        The token is strictly greater than any previously issued for this
        resource_id — this is the core fencing guarantee.

        Args:
            resource_id: The resource to lease.
            agent_id: The agent acquiring the lease.
            at: The HLC timestamp of the acquisition.
            ttl_events: Optional custom TTL (defaults to default_ttl_events).

        Returns:
            A FencingLease with the new token.

        Raises:
            ValueError: If the resource already has an active, non-expired lease
                       held by a different agent.
        """
        # Check for existing active lease
        current = self._backend.get_active(resource_id)
        if current is not None and not current.is_expired() and current.owner_agent_id != agent_id:
            raise ValueError(
                f"Resource {resource_id} already leased to {current.owner_agent_id} "
                f"(token={current.fencing_token}). Release or wait for expiry."
            )

        # Issue strictly increasing token
        token = self._backend.next_token(resource_id)
        lease = FencingLease(
            resource_id=resource_id,
            owner_agent_id=agent_id,
            fencing_token=token,
            issued_at=at,
            lease_ttl_events=ttl_events or self._default_ttl_events,
            events_since_issued=0,
        )

        self._backend.set_active(resource_id, lease)
        logger.info(
            "Lease acquired: %s -> %s (token=%d, ttl=%d events)",
            resource_id, agent_id, token, lease.lease_ttl_events,
        )
        return lease

    def release(self, resource_id: str, agent_id: str) -> bool:
        """Explicitly release a lease.

        Args:
            resource_id: The resource to release.
            agent_id: The agent releasing (must be the current owner).

        Returns:
            True if the lease was released, False if not owner or no lease.
        """
        current = self._backend.get_active(resource_id)
        if current is None or current.owner_agent_id != agent_id:
            return False

        self._backend.set_active(resource_id, None)
        logger.info("Lease released: %s by %s (token=%d)", resource_id, agent_id, current.fencing_token)
        return True

    def expire_if_stale(self, resource_id: str) -> FencingLease | None:
        """Expire the lease if its per-resource event-count TTL has been exceeded.

        Patch §3: Uses per-resource event counter, not the bus's global count.

        Returns:
            The expired lease if one was expired, None otherwise.
        """
        current = self._backend.get_active(resource_id)
        if current is None:
            return None

        if current.is_expired():
            self._backend.set_active(resource_id, None)
            logger.info(
                "Lease expired: %s held by %s (token=%d, events=%d/%d)",
                resource_id, current.owner_agent_id, current.fencing_token,
                current.events_since_issued, current.lease_ttl_events,
            )
            return current

        return None

    def tick_resource(self, resource_id: str) -> None:
        """Increment the per-resource event counter for lease TTL tracking.

        Called by the bus subscriber on each event for this resource.
        Updates the active lease's events_since_issued counter.
        """
        current = self._backend.get_active(resource_id)
        if current is not None:
            current.events_since_issued += 1

    def get_current_lease(self, resource_id: str) -> FencingLease | None:
        """Return the active lease for a resource, or None if none/expired."""
        lease = self._backend.get_active(resource_id)
        if lease is not None and lease.is_expired():
            return None
        return lease

    def get_all_leases(self) -> dict[str, FencingLease]:
        """Return all active (non-expired) leases."""
        if isinstance(self._backend, InMemoryLeaseBackend):
            return {
                rid: lease
                for rid, lease in self._backend._active.items()
                if not lease.is_expired()
            }
        return {}
