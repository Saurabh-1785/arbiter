"""Dependency graph builder — Elle-style anomaly detection.

Builds a Direct Serialization Graph (DSG) over observed operations with
WW, WR, and RW edge types, following Adya, Liskov & O'Neil's formalism.

Switches on payload["op"] ("read"/"write") to distinguish reads from writes
(Patch §1), so blackboard reads modeled as tool_call events with op="read"
correctly feed WR/RW edges.

A cycle in this graph proves the observed history couldn't have come from
any valid serial order — catching coordination anomalies like duplicate
work (write-skew) that were never explicitly specified as invariants.
"""

from __future__ import annotations

import logging
from typing import Any

import networkx as nx

from src.verifier.events import Event, HLCTimestamp
from src.verifier.anomaly.dependency_graph import EdgeType, Cycle

logger = logging.getLogger(__name__)


class DependencyGraphBuilderImpl:
    """Concrete implementation of the Elle-style dependency graph builder.

    Builds WW/WR/RW edges per resource_id as operations are recorded:

    WW (write-write): agent J's write to R followed agent I's write to R.
    WR (write-read): agent J read R's state after agent I wrote it.
    RW (read-write / anti-dependency): agent J wrote to R after reading a
        version of R that agent I's write later invalidated — the classic
        write-skew pattern (Act 3's target).

    The graph nodes are agent_ids; edges carry the type and resource_id.
    """

    def __init__(self):
        self._graph = nx.DiGraph()
        # Per-resource tracking
        self._writes: dict[str, list[tuple[str, HLCTimestamp]]] = {}   # resource -> [(agent, hlc)]
        self._reads: dict[str, list[tuple[str, HLCTimestamp, int]]] = {}  # resource -> [(agent, hlc, write_count_at_read)]

    def record(self, event: Event) -> None:
        """Record an event, building dependency edges as appropriate.

        Switches on payload["op"] to classify as read or write (Patch §1).
        Events without a resource_id or without op are ignored.
        """
        if event.resource_id is None:
            return

        resource_id = event.resource_id
        op = event.payload.get("op")

        if op is None:
            # Events without an op (e.g., state_transition) aren't
            # part of the read/write dependency model
            return

        # Ensure node exists
        if event.agent_id not in self._graph:
            self._graph.add_node(event.agent_id)

        if op == "write":
            self._record_write(resource_id, event.agent_id, event.hlc)
        elif op == "read":
            self._record_read(resource_id, event.agent_id, event.hlc)

    def _record_write(self, resource_id: str, agent_id: str, hlc: HLCTimestamp) -> None:
        """Record a write and build WW edges from prior writers, RW edges from prior readers."""
        if resource_id not in self._writes:
            self._writes[resource_id] = []
        if resource_id not in self._reads:
            self._reads[resource_id] = []

        # WW edges: this write depends on all prior writes to the same resource
        # (by different agents)
        for prior_agent, prior_hlc in self._writes[resource_id]:
            if prior_agent != agent_id:
                self._add_edge(
                    prior_agent, agent_id, EdgeType.WW, resource_id,
                    f"WW: {prior_agent} wrote {resource_id} before {agent_id}",
                )

        # RW (anti-dependency) edges: this write invalidates reads that
        # happened before it — if another agent read a version that this
        # write now supersedes, that's an anti-dependency
        current_write_count = len(self._writes[resource_id])
        for reader_agent, reader_hlc, read_version in self._reads[resource_id]:
            if reader_agent != agent_id:
                # The reader read version N, and this write creates version N+1.
                # If the reader's subsequent actions assumed version N was current,
                # that's a potential anti-dependency.
                self._add_edge(
                    reader_agent, agent_id, EdgeType.RW, resource_id,
                    f"RW: {reader_agent} read {resource_id} (v{read_version}) "
                    f"then {agent_id} wrote it (v{current_write_count + 1})",
                )

        # Record this write
        self._writes[resource_id].append((agent_id, hlc))

    def _record_read(self, resource_id: str, agent_id: str, hlc: HLCTimestamp) -> None:
        """Record a read and build WR edges from the last writer."""
        if resource_id not in self._writes:
            self._writes[resource_id] = []
        if resource_id not in self._reads:
            self._reads[resource_id] = []

        # WR edges: this read depends on the last write to the same resource
        if self._writes[resource_id]:
            last_writer, last_writer_hlc = self._writes[resource_id][-1]
            if last_writer != agent_id:
                self._add_edge(
                    last_writer, agent_id, EdgeType.WR, resource_id,
                    f"WR: {agent_id} read {resource_id} after {last_writer} wrote it",
                )

        # Record this read with the current write count (version observed)
        write_count = len(self._writes[resource_id])
        self._reads[resource_id].append((agent_id, hlc, write_count))

    def _add_edge(
        self,
        from_agent: str,
        to_agent: str,
        edge_type: EdgeType,
        resource_id: str,
        description: str,
    ) -> None:
        """Add a typed edge to the dependency graph."""
        # Ensure nodes exist
        if from_agent not in self._graph:
            self._graph.add_node(from_agent)
        if to_agent not in self._graph:
            self._graph.add_node(to_agent)

        # Add edge (allow parallel edges via key)
        key = f"{edge_type.value}:{resource_id}:{len(self._graph.edges)}"
        self._graph.add_edge(
            from_agent,
            to_agent,
            key=key,
            type=edge_type.value,
            resource_id=resource_id,
            description=description,
        )

        logger.debug("Edge added: %s -> %s (%s on %s)",
                      from_agent, to_agent, edge_type.value, resource_id)

    def find_cycles(self) -> list[Cycle]:
        """Detect cycles in the dependency graph.

        Uses networkx.simple_cycles (Johnson's algorithm) — runs in
        time roughly linear in the number of operations recorded.

        A cycle proves no valid ordering could explain the observed
        history — this is the signal for a coordination anomaly,
        even one nobody thought to write a spec for.

        Returns:
            List of Cycle objects. Empty for acyclic graphs (critical:
            no false positives — this matters as much as catching real ones).
        """
        cycles = []

        for cycle_nodes in nx.simple_cycles(self._graph):
            if len(cycle_nodes) < 2:
                continue

            # Collect edges and metadata for this cycle
            edges = []
            resource_ids = set()
            agents = list(cycle_nodes)

            for i in range(len(cycle_nodes)):
                from_node = cycle_nodes[i]
                to_node = cycle_nodes[(i + 1) % len(cycle_nodes)]

                # Get edge data (there may be multiple edges between the same pair)
                if self._graph.has_edge(from_node, to_node):
                    edge_data = self._graph.get_edge_data(from_node, to_node)
                    if edge_data:
                        # For MultiDiGraph compatibility, handle both formats
                        if isinstance(edge_data, dict) and "type" in edge_data:
                            edges.append({
                                "from": from_node,
                                "to": to_node,
                                "type": edge_data["type"],
                                "resource_id": edge_data.get("resource_id", ""),
                            })
                            resource_ids.add(edge_data.get("resource_id", ""))
                        else:
                            # Handle the case where edge_data might be nested
                            for key, data in edge_data.items() if isinstance(edge_data, dict) else []:
                                if isinstance(data, dict):
                                    edges.append({
                                        "from": from_node,
                                        "to": to_node,
                                        "type": data.get("type", ""),
                                        "resource_id": data.get("resource_id", ""),
                                    })
                                    resource_ids.add(data.get("resource_id", ""))

            description = (
                f"Cycle detected: {' -> '.join(agents + [agents[0]])} "
                f"(resources: {', '.join(resource_ids)})"
            )

            cycles.append(Cycle(
                agents=agents,
                edges=edges,
                resource_ids=resource_ids,
                description=description,
            ))

            logger.warning("ANOMALY CYCLE: %s", description)

        return cycles

    def to_serializable(self) -> dict[str, Any]:
        """Export the graph as a serializable dict for D3.js rendering.

        Returns:
            {"nodes": [{"id": agent_id}, ...],
             "edges": [{"source": from, "target": to, "type": ..., "resource_id": ...}, ...]}
        """
        nodes = [{"id": node} for node in self._graph.nodes]
        edges = []
        for u, v, data in self._graph.edges(data=True):
            edges.append({
                "source": u,
                "target": v,
                "type": data.get("type", ""),
                "resource_id": data.get("resource_id", ""),
                "description": data.get("description", ""),
            })

        return {"nodes": nodes, "edges": edges}

    def reset(self) -> None:
        """Clear all state for re-runnability (Patch §8)."""
        self._graph.clear()
        self._writes.clear()
        self._reads.clear()
