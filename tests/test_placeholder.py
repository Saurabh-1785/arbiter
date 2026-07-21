"""Phase 0 placeholder tests — verify all schemas are importable and constructible."""

from backend.verifier.events import Event, HLCTimestamp, TransportType, EventKind
from backend.verifier.lease.fencing import (
    FencingLease, LeaseBackend, InMemoryLeaseBackend, LeaseManagerProtocol,
)
from backend.verifier.lease.protected_resource import (
    WriteResult, WriteStatus, ProtectedResourceProtocol,
)
from backend.verifier.capability.tokens import (
    CapabilityToken, CapabilityStoreProtocol,
)
from backend.verifier.spec.state_machine import (
    TransitionResult, TransitionOutcome, MachineInstance, EngineContext,
    GuardFn, StateMachineEngineProtocol,
)
from backend.verifier.anomaly.dependency_graph import (
    EdgeType, Cycle, DependencyGraphBuilderProtocol,
)


def test_hlc_timestamp_constructible():
    """HLCTimestamp can be created and compared."""
    ts1 = HLCTimestamp(l=1000, c=0)
    ts2 = HLCTimestamp(l=1000, c=1)
    ts3 = HLCTimestamp(l=1001, c=0)
    assert ts1 < ts2 < ts3
    assert ts1 == HLCTimestamp(l=1000, c=0)
    assert ts1 != ts2


def test_hlc_timestamp_serialization():
    """HLCTimestamp round-trips through to_dict/from_dict."""
    ts = HLCTimestamp(l=1234567890, c=42)
    d = ts.to_dict()
    assert d == {"l": 1234567890, "c": 42}
    assert HLCTimestamp.from_dict(d) == ts


def test_event_constructible():
    """Event can be created via factory and serialized."""
    ts = HLCTimestamp(l=1000, c=0)
    event = Event.create(
        agent_id="agent-A",
        transport="grpc",
        hlc=ts,
        kind="task_claim",
        resource_id="task-42",
        payload={"fencing_token": 1, "op": "write"},
    )
    assert event.agent_id == "agent-A"
    assert event.transport == "grpc"
    assert event.kind == "task_claim"
    assert event.resource_id == "task-42"
    assert event.payload["fencing_token"] == 1
    assert event.event_id  # uuid was generated

    # Round-trip serialization
    d = event.to_dict()
    event2 = Event.from_dict(d)
    assert event2.agent_id == event.agent_id
    assert event2.hlc == event.hlc
    assert event2.kind == event.kind


def test_fencing_conflict_event():
    """fencing_conflict event kind is valid."""
    ts = HLCTimestamp(l=1000, c=0)
    event = Event.create(
        agent_id="agent-A",
        transport="grpc",
        hlc=ts,
        kind="fencing_conflict",
        resource_id="task-42",
        payload={"rejected_token": 1, "highest_token": 2},
    )
    assert event.kind == "fencing_conflict"
    assert event.payload["rejected_token"] == 1


def test_fencing_lease_constructible():
    """FencingLease can be created and checked for expiry."""
    ts = HLCTimestamp(l=1000, c=0)
    lease = FencingLease(
        resource_id="task-42",
        owner_agent_id="agent-A",
        fencing_token=1,
        issued_at=ts,
        lease_ttl_events=10,
        events_since_issued=0,
    )
    assert not lease.is_expired()
    lease.events_since_issued = 10
    assert lease.is_expired()


def test_in_memory_lease_backend():
    """InMemoryLeaseBackend issues strictly increasing tokens."""
    backend = InMemoryLeaseBackend()
    t1 = backend.next_token("task-42")
    t2 = backend.next_token("task-42")
    t3 = backend.next_token("task-42")
    assert t1 < t2 < t3
    assert t1 == 1

    # Different resource starts at 1
    t_other = backend.next_token("task-99")
    assert t_other == 1


def test_write_result_constructible():
    """WriteResult can be created for both accepted and rejected writes."""
    accepted = WriteResult(
        status=WriteStatus.ACCEPTED,
        resource_id="task-42",
        fencing_token=2,
        payload={"data": "hello"},
    )
    assert accepted.accepted

    rejected = WriteResult(
        status=WriteStatus.REJECTED_STALE_TOKEN,
        resource_id="task-42",
        fencing_token=1,
        payload={"data": "stale"},
        highest_accepted_token=2,
    )
    assert not rejected.accepted


def test_capability_token_constructible():
    """CapabilityToken can be created and serialized."""
    ts = HLCTimestamp(l=1000, c=0)
    token = CapabilityToken(
        token_id="tok-123",
        agent_id="agent-A",
        scope="tool:payment.charge",
        issued_at=ts,
    )
    assert not token.revoked
    token.revoked = True
    assert token.revoked

    d = token.to_dict()
    assert d["scope"] == "tool:payment.charge"
    assert d["revoked"] is True


def test_transition_result_values():
    """TransitionResult enum has expected values."""
    assert TransitionResult.TRANSITIONED.value == "transitioned"
    assert TransitionResult.NO_MATCHING_TRANSITION.value == "no_matching_transition"
    assert TransitionResult.TIMED_OUT.value == "timed_out"


def test_machine_instance_constructible():
    """MachineInstance tracks per-resource state with owner."""
    inst = MachineInstance(
        resource_id="task-42",
        state="Idle",
        current_owner=None,
        entered_state_at_local_count=0,
        local_event_count=0,
    )
    assert inst.state == "Idle"
    assert inst.current_owner is None

    d = inst.to_dict()
    assert d["resource_id"] == "task-42"
    assert d["state"] == "Idle"


def test_engine_context_constructible():
    """EngineContext can be constructed with None refs (for testing)."""
    ctx = EngineContext()
    assert ctx.protected_resource is None
    assert ctx.lease_manager is None


def test_edge_type_values():
    """EdgeType enum has expected values."""
    assert EdgeType.WW.value == "ww"
    assert EdgeType.WR.value == "wr"
    assert EdgeType.RW.value == "rw"


def test_cycle_constructible():
    """Cycle can be created and serialized."""
    cycle = Cycle(
        agents=["agent-A", "agent-B"],
        edges=[
            {"from": "agent-A", "to": "agent-B", "type": "rw", "resource_id": "task-42"},
            {"from": "agent-B", "to": "agent-A", "type": "rw", "resource_id": "task-42"},
        ],
        resource_ids={"task-42"},
        description="Write-skew cycle between A and B on task-42",
    )
    d = cycle.to_dict()
    assert len(d["agents"]) == 2
    assert len(d["edges"]) == 2
    assert "task-42" in d["resource_ids"]
