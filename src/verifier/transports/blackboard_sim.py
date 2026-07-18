"""Shared blackboard simulated transport adapter (Redis/Postgres-style).

Models a shared key-value store where agents read and write shared state.
This is the transport that enables Act 3's write-skew scenario — two agents
reading the same blackboard key at nearly the same causal moment.

Reads are modeled as tool_call events with payload["op"] = "read" (Patch §1),
and writes as events with payload["op"] = "write". The DependencyGraphBuilder
switches on payload["op"] to build WR/RW edges.
"""

from __future__ import annotations

import logging
from typing import Any

from src.verifier.events import Event, HLCTimestamp
from src.verifier.bus import EventBus

logger = logging.getLogger(__name__)


class BlackboardSimTransport:
    """Simulated shared blackboard transport — models a shared KV store.

    In production, this would be replaced by intercepting actual Redis/Postgres
    queries via eBPF or query-level instrumentation (Section 9 non-goal).
    """

    TRANSPORT_NAME = "blackboard"

    def __init__(self, bus: EventBus):
        self._bus = bus
        self._store: dict[str, dict[str, Any]] = {}  # resource_id -> current state
        self._version: dict[str, int] = {}  # resource_id -> write version counter

    def _get_version(self, resource_id: str) -> int:
        """Get the current version for a resource."""
        return self._version.get(resource_id, 0)

    async def read(
        self,
        agent_id: str,
        resource_id: str,
        hlc: HLCTimestamp,
        capability_token: str | None = None,
    ) -> tuple[dict[str, Any] | None, Event]:
        """Read the current state of a resource from the blackboard.

        Emits a tool_call event with op="read" so the dependency graph
        can build WR/RW edges (Patch §1).

        Returns:
            A tuple of (current_value, emitted_event).
        """
        current_value = self._store.get(resource_id)
        current_version = self._get_version(resource_id)

        event = Event.create(
            agent_id=agent_id,
            transport=self.TRANSPORT_NAME,
            hlc=hlc,
            kind="tool_call",
            resource_id=resource_id,
            payload={
                "op": "read",
                "tool_name": "blackboard.read",
                "args": {"resource_id": resource_id},
                "version_read": current_version,
                "value": current_value,
            },
            capability_token=capability_token,
        )

        logger.info(
            "[Blackboard] %s reads %s (version=%d)",
            agent_id, resource_id, current_version,
        )

        emitted = await self._bus.emit(event)
        return current_value, emitted

    async def write(
        self,
        agent_id: str,
        resource_id: str,
        data: dict[str, Any],
        hlc: HLCTimestamp,
        kind: str = "tool_call",
        capability_token: str | None = None,
    ) -> Event:
        """Write a value to the blackboard for a resource.

        Emits an event with op="write". Increments the version counter.

        Returns:
            The emitted Event.
        """
        self._version[resource_id] = self._get_version(resource_id) + 1
        self._store[resource_id] = data
        new_version = self._version[resource_id]

        event = Event.create(
            agent_id=agent_id,
            transport=self.TRANSPORT_NAME,
            hlc=hlc,
            kind=kind,
            resource_id=resource_id,
            payload={
                "op": "write",
                "tool_name": "blackboard.write",
                "args": {"resource_id": resource_id, "data": data},
                "version_written": new_version,
            },
            capability_token=capability_token,
        )

        logger.info(
            "[Blackboard] %s writes %s (version=%d)",
            agent_id, resource_id, new_version,
        )

        return await self._bus.emit(event)

    def get_current(self, resource_id: str) -> dict[str, Any] | None:
        """Get current value without emitting an event (internal use)."""
        return self._store.get(resource_id)
