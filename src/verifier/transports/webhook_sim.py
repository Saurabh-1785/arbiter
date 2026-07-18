"""Webhook simulated transport adapter.

Models HTTP webhook calls between agents — one agent POSTs an event to
another agent's webhook endpoint. For the demo, this is an in-memory call.
"""

from __future__ import annotations

import logging
from typing import Any

from src.verifier.events import Event, HLCTimestamp
from src.verifier.bus import EventBus

logger = logging.getLogger(__name__)


class WebhookSimTransport:
    """Simulated webhook transport — models HTTP POST callbacks between agents.

    In production, this would be replaced by intercepting actual HTTP traffic
    via a reverse proxy or eBPF (Section 9 non-goal).
    """

    TRANSPORT_NAME = "webhook"

    def __init__(self, bus: EventBus):
        self._bus = bus
        self._endpoints: dict[str, list[Event]] = {}  # agent_id -> received events

    async def post(
        self,
        from_agent: str,
        to_url: str,
        hlc: HLCTimestamp,
        kind: str,
        resource_id: str | None = None,
        payload: dict[str, Any] | None = None,
        capability_token: str | None = None,
    ) -> Event:
        """Simulate a webhook POST from one agent to a URL endpoint.

        Args:
            from_agent: The agent sending the webhook.
            to_url: The target URL (in simulation, just a string identifier).
            hlc: The sender's current HLC timestamp.
            kind: Event kind.
            resource_id: Optional resource ID.
            payload: Kind-specific data.
            capability_token: Optional capability token.

        Returns:
            The emitted Event with merged HLC timestamp.
        """
        full_payload = dict(payload or {})
        full_payload["to_url"] = to_url

        event = Event.create(
            agent_id=from_agent,
            transport=self.TRANSPORT_NAME,
            hlc=hlc,
            kind=kind,
            resource_id=resource_id,
            payload=full_payload,
            capability_token=capability_token,
        )

        logger.info(
            "[Webhook] %s -> %s: %s (resource=%s)",
            from_agent, to_url, kind, resource_id,
        )

        emitted = await self._bus.emit(event)

        # Store at the "endpoint" for the receiving agent to process
        target_agent = to_url.split("/")[-1] if "/" in to_url else to_url
        if target_agent not in self._endpoints:
            self._endpoints[target_agent] = []
        self._endpoints[target_agent].append(emitted)

        return emitted

    def get_received(self, agent_id: str) -> list[Event]:
        """Get all events received at an agent's webhook endpoint."""
        return self._endpoints.get(agent_id, [])
