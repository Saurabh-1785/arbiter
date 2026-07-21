"""Tests for fencing-token lease management (Section 10).

Tests cover:
- Strictly increasing tokens per resource
- Stale (lower) token write rejected even with out-of-order arrival
- Lease expiry (event-count TTL) works
- Re-acquire after expiry bumps the token
- End-to-end: stale agent's late write is rejected outright
"""

from backend.verifier.events import HLCTimestamp
from backend.verifier.lease.fencing import FencingLease, InMemoryLeaseBackend
from backend.verifier.lease.lease_manager import LeaseManager
from backend.verifier.lease.protected_resource import WriteStatus
from backend.verifier.lease.protected_resource_impl import ProtectedResourceImpl


class TestFencingTokenMonotonicity:
    """Fencing tokens must be strictly increasing per resource."""

    def test_strictly_increasing_tokens(self):
        """acquire() issues strictly increasing tokens per resource_id."""
        manager = LeaseManager(default_ttl_events=100)
        ts = HLCTimestamp(l=1000, c=0)

        lease1 = manager.acquire("task-42", "agent-A", ts)
        manager.release("task-42", "agent-A")

        lease2 = manager.acquire("task-42", "agent-B", ts)
        manager.release("task-42", "agent-B")

        lease3 = manager.acquire("task-42", "agent-A", ts)

        assert lease1.fencing_token < lease2.fencing_token < lease3.fencing_token
        assert lease1.fencing_token == 1
        assert lease2.fencing_token == 2
        assert lease3.fencing_token == 3

    def test_independent_tokens_per_resource(self):
        """Different resources have independent token sequences."""
        manager = LeaseManager(default_ttl_events=100)
        ts = HLCTimestamp(l=1000, c=0)

        lease_a = manager.acquire("task-42", "agent-A", ts)
        lease_b = manager.acquire("task-99", "agent-B", ts)

        assert lease_a.fencing_token == 1
        assert lease_b.fencing_token == 1  # independent sequence

    def test_cannot_acquire_active_lease_different_agent(self):
        """Cannot acquire a lease already held by another agent."""
        manager = LeaseManager(default_ttl_events=100)
        ts = HLCTimestamp(l=1000, c=0)

        manager.acquire("task-42", "agent-A", ts)

        try:
            manager.acquire("task-42", "agent-B", ts)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "already leased" in str(e)

    def test_same_agent_can_reacquire(self):
        """Same agent can re-acquire its own lease (idempotent)."""
        manager = LeaseManager(default_ttl_events=100)
        ts = HLCTimestamp(l=1000, c=0)

        lease1 = manager.acquire("task-42", "agent-A", ts)
        lease2 = manager.acquire("task-42", "agent-A", ts)

        assert lease2.fencing_token > lease1.fencing_token


class TestProtectedResourceFencing:
    """ProtectedResource must reject stale token writes."""

    def test_accept_valid_write(self):
        """Write with a valid token is accepted."""
        resource = ProtectedResourceImpl()
        result = resource.write("task-42", fencing_token=1, payload={"data": "hello"})
        assert result.accepted
        assert result.status == WriteStatus.ACCEPTED

    def test_reject_stale_token(self):
        """Write with a stale (lower) token is rejected — not merely logged."""
        resource = ProtectedResourceImpl()

        # Accept a write with token 2
        result1 = resource.write("task-42", fencing_token=2, payload={"data": "current"})
        assert result1.accepted

        # Reject a write with stale token 1
        result2 = resource.write("task-42", fencing_token=1, payload={"data": "stale"})
        assert not result2.accepted
        assert result2.status == WriteStatus.REJECTED_STALE_TOKEN
        assert result2.highest_accepted_token == 2

    def test_reject_stale_token_out_of_order_arrival(self):
        """A stale token write is rejected even if it arrives out of order.

        This tests the specific scenario from Section 10: "rejected even if
        it arrives out of order" — the token itself determines validity,
        not arrival order.
        """
        resource = ProtectedResourceImpl()

        # Token 3 arrives first (out of order)
        resource.write("task-42", fencing_token=3, payload={"data": "from-token-3"})

        # Token 2 arrives later (out of order but still stale)
        result = resource.write("task-42", fencing_token=2, payload={"data": "from-token-2"})
        assert not result.accepted
        assert result.status == WriteStatus.REJECTED_STALE_TOKEN

        # Token 1 also rejected
        result = resource.write("task-42", fencing_token=1, payload={"data": "from-token-1"})
        assert not result.accepted

    def test_equal_token_accepted(self):
        """A write with the same token as the highest is accepted (idempotent)."""
        resource = ProtectedResourceImpl()
        resource.write("task-42", fencing_token=2, payload={"data": "first"})
        result = resource.write("task-42", fencing_token=2, payload={"data": "second"})
        assert result.accepted

    def test_highest_accepted(self):
        """highest_accepted() returns the correct value."""
        resource = ProtectedResourceImpl()
        assert resource.highest_accepted("task-42") == 0

        resource.write("task-42", fencing_token=5, payload={})
        assert resource.highest_accepted("task-42") == 5

    def test_audit_log(self):
        """Accepted writes are recorded in the audit log."""
        resource = ProtectedResourceImpl()
        resource.write("task-42", fencing_token=1, payload={"data": "first"})
        resource.write("task-42", fencing_token=2, payload={"data": "second"})
        resource.write("task-42", fencing_token=0, payload={"data": "rejected"})

        writes = resource.get_writes("task-42")
        assert len(writes) == 2  # only accepted writes
        assert writes[0]["payload"]["data"] == "first"
        assert writes[1]["payload"]["data"] == "second"


class TestLeaseExpiry:
    """Lease expiry via per-resource event-count TTL."""

    def test_lease_expires_after_ttl(self):
        """Lease expires after enough per-resource events."""
        manager = LeaseManager(default_ttl_events=5)
        ts = HLCTimestamp(l=1000, c=0)

        lease = manager.acquire("task-42", "agent-A", ts)
        assert not lease.is_expired()

        # Tick 5 times (reaching TTL)
        for _ in range(5):
            manager.tick_resource("task-42")

        assert lease.is_expired()

        # expire_if_stale should now clear it
        expired = manager.expire_if_stale("task-42")
        assert expired is not None
        assert expired.owner_agent_id == "agent-A"

    def test_reacquire_after_expiry_bumps_token(self):
        """Re-acquiring after expiry issues a higher token."""
        manager = LeaseManager(default_ttl_events=3)
        ts = HLCTimestamp(l=1000, c=0)

        lease1 = manager.acquire("task-42", "agent-A", ts)
        token1 = lease1.fencing_token

        # Expire the lease
        for _ in range(3):
            manager.tick_resource("task-42")
        manager.expire_if_stale("task-42")

        # Re-acquire with a different agent
        lease2 = manager.acquire("task-42", "agent-B", ts)
        assert lease2.fencing_token > token1

    def test_tick_only_affects_target_resource(self):
        """Ticking one resource doesn't affect another's TTL."""
        manager = LeaseManager(default_ttl_events=3)
        ts = HLCTimestamp(l=1000, c=0)

        lease_42 = manager.acquire("task-42", "agent-A", ts)
        lease_99 = manager.acquire("task-99", "agent-B", ts)

        # Tick task-42 past TTL
        for _ in range(5):
            manager.tick_resource("task-42")

        assert lease_42.is_expired()
        assert not lease_99.is_expired()  # task-99 unaffected


class TestEndToEndFencing:
    """End-to-end: stale agent's late write is rejected outright."""

    def test_stale_agent_write_rejected(self):
        """Full Act 2 scenario: A acquires, hangs, B acquires after expiry,
        B writes successfully, A's stale write is rejected.

        This is the single most important test for the fencing mechanism —
        it proves the hard invariant is structurally enforced.
        """
        manager = LeaseManager(default_ttl_events=5)
        resource = ProtectedResourceImpl()
        ts = HLCTimestamp(l=1000, c=0)

        # Agent A acquires lease (token=1)
        lease_a = manager.acquire("task-42", "agent-A", ts)
        assert lease_a.fencing_token == 1

        # Agent A hangs — per-resource events tick past TTL
        for _ in range(5):
            manager.tick_resource("task-42")

        # Lease expires
        expired = manager.expire_if_stale("task-42")
        assert expired is not None

        # Agent B acquires new lease (token=2)
        lease_b = manager.acquire("task-42", "agent-B", ts)
        assert lease_b.fencing_token == 2

        # Agent B writes successfully
        result_b = resource.write("task-42", lease_b.fencing_token, {"status": "completed_by_B"})
        assert result_b.accepted

        # Agent A "wakes up" and attempts write with stale token=1
        result_a = resource.write("task-42", lease_a.fencing_token, {"status": "stale_from_A"})

        # THE KEY ASSERTION: A's write is REJECTED, not logged
        assert not result_a.accepted
        assert result_a.status == WriteStatus.REJECTED_STALE_TOKEN
        assert result_a.highest_accepted_token == 2

        # Only B's write is in the audit log
        writes = resource.get_writes("task-42")
        assert len(writes) == 1
        assert writes[0]["payload"]["status"] == "completed_by_B"

    def test_explicit_release_allows_reacquire(self):
        """Explicit release allows a different agent to acquire."""
        manager = LeaseManager(default_ttl_events=100)
        ts = HLCTimestamp(l=1000, c=0)

        manager.acquire("task-42", "agent-A", ts)
        assert manager.release("task-42", "agent-A")

        # Now B can acquire
        lease_b = manager.acquire("task-42", "agent-B", ts)
        assert lease_b.fencing_token == 2  # strictly increasing
