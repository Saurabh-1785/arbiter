"""Stdout simulated transport adapter.

Models agents communicating via stdout piping — the simplest transport,
where an agent just emits structured events to stdout and a CLI orchestrator
captures them. For the demo, this logs to Python's logging system and
emits through the bus.
"""

from __future__ import annotations

import logging
from typing import Any

from src.verifier.events import Event, HLCTimestamp
from src.verifier.bus import EventBus

logger = logging.getLogger(__name__)


class StdoutSimTransport:
    """Simulated stdout transport — models CLI stdout piping.

    In production, this would capture actual stdout streams from
    agent processes (Section 9 non-goal).
    """

    TRANSPORT_NAME = "stdout"

    def __init__(self, bus: EventBus):
        self._bus = bus
        self._output_log: list[str] = []

    async def emit(
        self,
        agent_id: str,
        hlc: HLCTimestamp,
        kind: str,
        message: str = "",
        resource_id: str | None = None,
        payload: dict[str, Any] | None = None,
        capability_token: str | None = None,
    ) -> Event:
        """Simulate an agent emitting a structured event to stdout.

        Args:
            agent_id: The emitting agent.
            hlc: The agent's current HLC timestamp.
            kind: Event kind.
            message: A human-readable message (stored in payload).
            resource_id: Optional resource ID.
            payload: Kind-specific data.
            capability_token: Optional capability token.

        Returns:
            The emitted Event with merged HLC timestamp.
        """
        full_payload = dict(payload or {})
        full_payload["message"] = message

        event = Event.create(
            agent_id=agent_id,
            transport=self.TRANSPORT_NAME,
            hlc=hlc,
            kind=kind,
            resource_id=resource_id,
            payload=full_payload,
            capability_token=capability_token,
        )

        # Log to "stdout"
        log_line = f"[stdout] {agent_id}: {kind} — {message}"
        self._output_log.append(log_line)
        logger.info(log_line)

        return await self._bus.emit(event)

    def get_output(self) -> list[str]:
        """Get all stdout output lines."""
        return list(self._output_log)
