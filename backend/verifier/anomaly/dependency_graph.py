"""Dependency graph and cycle detection contracts — Elle-style anomaly detection.

Borrows from Elle, the checker Kyle Kingsbury and Peter Alvaro built for Jepsen
(VLDB 2020) to catch database transactional-isolation bugs. Builds a dependency
graph over observed operations following Adya, Liskov & O'Neil's Direct
Serialization Graph (DSG) formalism with WW, WR, and RW edge types, then
searches for cycles.

A cycle proves the observed history couldn't have come from any valid serial
order — this catches coordination anomalies (like duplicate work / write-skew)
that were never explicitly specified as invariants.

The graph builder switches on payload["op"] ("read"/"write"), not on Event.kind,
so that blackboard reads modeled as tool_call events with op="read" correctly
feed WR/RW edges without schema changes (Patch §1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from backend.verifier.events import Event


# ---------------------------------------------------------------------------
# Edge types — Adya, Liskov & O'Neil's DSG formalism
# ---------------------------------------------------------------------------

class EdgeType(Enum):
    """Dependency edge types in the Direct Serialization Graph.

    WW: agent J's write to R followed agent I's write to R.
    WR: agent J observed (read) R's state after agent I wrote it.
    RW: agent J wrote to R after reading a version of R that agent I's
        write later invalidated — the classic write-skew pattern.
    """
    WW = "ww"  # write-write dependency
    WR = "wr"  # write-read dependency
    RW = "rw"  # read-write anti-dependency (write-skew)


# ---------------------------------------------------------------------------
# Cycle — a detected anomaly
# ---------------------------------------------------------------------------

@dataclass
class Cycle:
    """A cycle detected in the dependency graph — proof of a coordination anomaly.

    Each cycle includes the participating agents, the edge types forming the
    cycle, and the resource_ids involved. Because it's just cycle detection,
    this runs in time roughly linear in the number of operations recorded.
    """

    agents: list[str]                    # agent_ids forming the cycle
    edges: list[dict[str, Any]]          # [{from, to, type, resource_id}, ...]
    resource_ids: set[str]               # resources involved
    description: str = ""                # human-readable explanation

    def to_dict(self) -> dict[str, Any]:
        """Serialize for WebSocket/dashboard transport."""
        return {
            "agents": self.agents,
            "edges": self.edges,
            "resource_ids": list(self.resource_ids),
            "description": self.description,
        }


# ---------------------------------------------------------------------------
# DependencyGraphBuilder protocol — Section 6.5
# ---------------------------------------------------------------------------

class DependencyGraphBuilderProtocol(Protocol):
    """Protocol for the dependency graph builder (Section 6.5).

    Builds WW/WR/RW edges per resource_id as in Section 3.5.
    Switches on payload["op"] to distinguish reads from writes.
    """

    def record(self, event: Event) -> None:
        """Record an event, building dependency edges as appropriate."""
        ...

    def find_cycles(self) -> list[Cycle]:
        """Detect cycles in the dependency graph.
        Should run in time roughly linear in events recorded."""
        ...

    def to_serializable(self) -> dict[str, Any]:
        """Export the graph as a serializable dict for D3.js rendering.
        Returns {"nodes": [...], "edges": [...]}."""
        ...

    def reset(self) -> None:
        """Clear the graph for re-runnability."""
        ...
