"""gRPC simulated transport adapter.

For the demo, this is a Python call under the hood, but it's named and shaped
like a real gRPC call so the "swap in real eBPF capture later" story is
credible (Section 9). The real gRPC version would capture actual protobuf
messages over HTTP/2.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.verifier.events import Event, HLCTimestamp
from backend.verifier.bus import EventBus
from backend.verifier.hlc import HLCClock

logger = logging.getLogger(__name__)


class GrpcSimTransport:
    """Simulated gRPC transport — models point-to-point RPC calls between agents.

    In production, this would be replaced by an eBPF-based interceptor
    capturing actual gRPC traffic (Section 9 non-goal).
    """

    TRANSPORT_NAME = "grpc"

    def __init__(self, bus: EventBus):
        self._bus = bus

    async def send(
        self,
        from_agent: str,
        to_agent: str,
        hlc: HLCTimestamp,
        kind: str,
        resource_id: str | None = None,
        payload: dict[str, Any] | None = None,
        capability_token: str | None = None,
    ) -> Event:
        """Simulate a gRPC call from one agent to another.

        Creates an Event tagged with transport="grpc" and emits it
        through the bus for HLC-stamping and fan-out.

        Args:
            from_agent: The sending agent's ID.
            to_agent: The receiving agent's ID (stored in payload).
            hlc: The sender's current HLC timestamp.
            kind: Event kind (e.g. "task_claim", "handoff_request").
            resource_id: Optional resource this event concerns.
            payload: Kind-specific data.
            capability_token: Optional capability token for tool-call gating.

        Returns:
            The emitted Event with merged HLC timestamp.
        """
        full_payload = dict(payload or {})
        full_payload["to_agent"] = to_agent

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
            "[gRPC] %s -> %s: %s (resource=%s)",
            from_agent, to_agent, kind, resource_id,
        )
        return await self._bus.emit(event)
