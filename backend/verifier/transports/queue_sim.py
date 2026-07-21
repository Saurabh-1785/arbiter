"""Message queue simulated transport adapter (NATS/Kafka-style).

Models publish/subscribe messaging where agents publish to topics and
consume messages asynchronously. For the demo, this is a simple in-memory
queue under the hood.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.verifier.events import Event, HLCTimestamp
from backend.verifier.bus import EventBus

logger = logging.getLogger(__name__)


class QueueSimTransport:
    """Simulated message queue transport — models pub/sub messaging.

    In production, this would be replaced by a NATS/Kafka consumer
    or eBPF-based message interceptor (Section 9 non-goal).
    """

    TRANSPORT_NAME = "queue"

    def __init__(self, bus: EventBus):
        self._bus = bus
        self._queues: dict[str, asyncio.Queue] = {}  # topic -> queue

    def _get_queue(self, topic: str) -> asyncio.Queue:
        """Get or create a queue for a topic."""
        if topic not in self._queues:
            self._queues[topic] = asyncio.Queue()
        return self._queues[topic]

    async def publish(
        self,
        agent_id: str,
        topic: str,
        hlc: HLCTimestamp,
        kind: str,
        resource_id: str | None = None,
        payload: dict[str, Any] | None = None,
        capability_token: str | None = None,
    ) -> Event:
        """Publish a message to a topic.

        Creates an Event tagged with transport="queue" and emits it
        through the bus. Also enqueues for any consumers.

        Returns:
            The emitted Event with merged HLC timestamp.
        """
        full_payload = dict(payload or {})
        full_payload["topic"] = topic

        event = Event.create(
            agent_id=agent_id,
            transport=self.TRANSPORT_NAME,
            hlc=hlc,
            kind=kind,
            resource_id=resource_id,
            payload=full_payload,
            capability_token=capability_token,
        )

        logger.info(
            "[Queue] %s published to '%s': %s (resource=%s)",
            agent_id, topic, kind, resource_id,
        )

        # Emit through bus first (HLC stamping)
        emitted = await self._bus.emit(event)

        # Then enqueue for topic consumers
        queue = self._get_queue(topic)
        await queue.put(emitted)

        return emitted

    async def consume(self, topic: str, timeout: float = 1.0) -> Event | None:
        """Consume a message from a topic.

        Args:
            topic: The topic to consume from.
            timeout: Maximum wait time in seconds.

        Returns:
            The next Event from the topic, or None on timeout.
        """
        queue = self._get_queue(topic)
        try:
            return await asyncio.wait_for(queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
