"""Tests for the dependency graph and cycle detector (Section 10).

Tests cover:
- Graph builder ingests claim/read/write operations and produces correctly-typed edges
- Cycle detector correctly flags a hand-constructed cyclic (write-skew-style) scenario
- Cycle detector reports ZERO cycles on the Act-1 clean scenario (no false positives)
- Output is serializable (for the dashboard to render as a node-link graph)
"""

from src.verifier.events import Event, HLCTimestamp
from src.verifier.anomaly.dependency_graph_impl import DependencyGraphBuilderImpl


def make_event(
    agent_id: str,
    resource_id: str,
    op: str,
    kind: str = "tool_call",
    hlc_l: int = 1000,
    hlc_c: int = 0,
) -> Event:
    """Helper to create test events for dependency graph testing."""
    payload = {"op": op}
    if kind == "tool_call":
        payload.update({"tool_name": "test", "args": {}})
    elif kind == "task_claim":
        payload.update({"fencing_token": 1})
    return Event.create(
        agent_id=agent_id,
        transport="blackboard",
        hlc=HLCTimestamp(l=hlc_l, c=hlc_c),
        kind=kind,
        resource_id=resource_id,
        payload=payload,
    )


class TestEdgeBuilding:
    """Graph builder should produce correctly-typed edges."""

    def test_ww_edges(self):
        """Write-write: sequential writes by different agents produce WW edges."""
        builder = DependencyGraphBuilderImpl()

        # Agent A writes to task-42
        builder.record(make_event("agent-A", "task-42", "write", hlc_c=0))
        # Agent B writes to task-42
        builder.record(make_event("agent-B", "task-42", "write", hlc_c=1))

        graph = builder.to_serializable()
        ww_edges = [e for e in graph["edges"] if e["type"] == "ww"]
        assert len(ww_edges) >= 1
        assert any(e["source"] == "agent-A" and e["target"] == "agent-B" for e in ww_edges)

    def test_wr_edges(self):
        """Write-read: a read after a write by a different agent produces WR edge."""
        builder = DependencyGraphBuilderImpl()

        # Agent A writes
        builder.record(make_event("agent-A", "task-42", "write", hlc_c=0))
        # Agent B reads
        builder.record(make_event("agent-B", "task-42", "read", hlc_c=1))

        graph = builder.to_serializable()
        wr_edges = [e for e in graph["edges"] if e["type"] == "wr"]
        assert len(wr_edges) >= 1
        assert any(e["source"] == "agent-A" and e["target"] == "agent-B" for e in wr_edges)

    def test_rw_edges(self):
        """Read-write (anti-dependency): a write after a read by a different agent
        produces RW edge — this is the write-skew pattern."""
        builder = DependencyGraphBuilderImpl()

        # Agent A reads (observes version 0 — no prior writes)
        builder.record(make_event("agent-A", "task-42", "read", hlc_c=0))
        # Agent B writes (creates version 1, invalidating A's read)
        builder.record(make_event("agent-B", "task-42", "write", hlc_c=1))

        graph = builder.to_serializable()
        rw_edges = [e for e in graph["edges"] if e["type"] == "rw"]
        assert len(rw_edges) >= 1
        assert any(e["source"] == "agent-A" and e["target"] == "agent-B" for e in rw_edges)

    def test_no_self_edges(self):
        """An agent's own reads and writes should not produce edges to itself."""
        builder = DependencyGraphBuilderImpl()

        builder.record(make_event("agent-A", "task-42", "write", hlc_c=0))
        builder.record(make_event("agent-A", "task-42", "read", hlc_c=1))
        builder.record(make_event("agent-A", "task-42", "write", hlc_c=2))

        graph = builder.to_serializable()
        self_edges = [e for e in graph["edges"] if e["source"] == e["target"]]
        assert len(self_edges) == 0


class TestCycleDetection:
    """Cycle detection should flag anomalies and not produce false positives."""

    def test_write_skew_cycle_detected(self):
        """Hand-constructed write-skew scenario produces a cycle.

        This models Act 3:
        1. Agent A reads task-42 (sees version 0)
        2. Agent C reads task-42 (sees version 0)  — concurrent reads
        3. Agent A writes task-42 (creates version 1, based on v0 read)
        4. Agent C writes task-42 (creates version 2, based on v0 read)

        This should produce:
        - RW edge: A read v0, then C wrote v1 (or vice versa)
        - RW edge: C read v0, then A wrote v1 (or vice versa)
        - The mutual RW edges form a CYCLE — proof of write-skew anomaly.
        """
        builder = DependencyGraphBuilderImpl()

        # Both agents read "task-42" at version 0 (concurrent reads)
        builder.record(make_event("agent-A", "task-42", "read", hlc_c=0))
        builder.record(make_event("agent-C", "task-42", "read", hlc_c=1))

        # Both agents then write (each based on their stale read)
        builder.record(make_event("agent-A", "task-42", "write", hlc_c=2))
        builder.record(make_event("agent-C", "task-42", "write", hlc_c=3))

        cycles = builder.find_cycles()
        assert len(cycles) >= 1, "Expected at least one cycle for write-skew scenario"

        # Verify the cycle involves both agents
        cycle_agents = set()
        for cycle in cycles:
            cycle_agents.update(cycle.agents)
        assert "agent-A" in cycle_agents
        assert "agent-C" in cycle_agents

    def test_clean_scenario_zero_cycles(self):
        """The Act-1 clean scenario should produce ZERO false positives.

        This is as important as catching real anomalies — a false positive
        in the clean scenario undermines trust in the entire detection system.
        """
        builder = DependencyGraphBuilderImpl()

        # Clean sequential handoff: A writes, B reads, B writes
        builder.record(make_event("agent-A", "task-42", "write", hlc_c=0))
        builder.record(make_event("agent-B", "task-42", "read", hlc_c=1))
        builder.record(make_event("agent-B", "task-42", "write", hlc_c=2))

        cycles = builder.find_cycles()
        assert len(cycles) == 0, f"Expected zero cycles, got {len(cycles)}: {cycles}"

    def test_sequential_writes_no_cycle(self):
        """Sequential writes by different agents: no cycle (just WW chain)."""
        builder = DependencyGraphBuilderImpl()

        builder.record(make_event("agent-A", "task-42", "write", hlc_c=0))
        builder.record(make_event("agent-B", "task-42", "write", hlc_c=1))
        builder.record(make_event("agent-C", "task-42", "write", hlc_c=2))

        cycles = builder.find_cycles()
        assert len(cycles) == 0

    def test_independent_resources_no_cycle(self):
        """Operations on independent resources should not create false cycles."""
        builder = DependencyGraphBuilderImpl()

        # A writes to task-42, B writes to task-99
        builder.record(make_event("agent-A", "task-42", "write", hlc_c=0))
        builder.record(make_event("agent-B", "task-99", "write", hlc_c=1))

        cycles = builder.find_cycles()
        assert len(cycles) == 0


class TestSerialization:
    """Graph output should be serializable for D3.js rendering."""

    def test_serializable_output(self):
        """to_serializable() returns a well-formed dict for D3.js."""
        builder = DependencyGraphBuilderImpl()

        builder.record(make_event("agent-A", "task-42", "write", hlc_c=0))
        builder.record(make_event("agent-B", "task-42", "read", hlc_c=1))

        output = builder.to_serializable()

        assert "nodes" in output
        assert "edges" in output
        assert len(output["nodes"]) == 2
        assert len(output["edges"]) >= 1

        # Nodes have id field
        for node in output["nodes"]:
            assert "id" in node

        # Edges have required fields
        for edge in output["edges"]:
            assert "source" in edge
            assert "target" in edge
            assert "type" in edge
            assert "resource_id" in edge

    def test_empty_graph_serializable(self):
        """An empty graph serializes cleanly."""
        builder = DependencyGraphBuilderImpl()
        output = builder.to_serializable()
        assert output == {"nodes": [], "edges": []}

    def test_reset_clears_state(self):
        """reset() clears all state for re-runnability."""
        builder = DependencyGraphBuilderImpl()
        builder.record(make_event("agent-A", "task-42", "write"))
        builder.reset()

        output = builder.to_serializable()
        assert output == {"nodes": [], "edges": []}
        assert builder.find_cycles() == []
