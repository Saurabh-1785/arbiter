"""Base agent class for ARBITER's toy agents.

Each agent is an asyncio task with its own HLC clock, bound to a primary
transport, and coordinating over shared task resources. The base class
provides the common infrastructure; concrete agents override run() with
their scripted demo behaviors.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.verifier.events import Event, HLCTimestamp, TransportType, EventKind
from backend.verifier.hlc import HLCClock
from backend.verifier.bus import EventBus
from backend.verifier.transports.grpc_sim import GrpcSimTransport
from backend.verifier.transports.queue_sim import QueueSimTransport
from backend.verifier.transports.blackboard_sim import BlackboardSimTransport
from backend.verifier.transports.webhook_sim import WebhookSimTransport
from backend.verifier.transports.stdout_sim import StdoutSimTransport

logger = logging.getLogger(__name__)


class BaseAgent:
    """Base class for toy agents in the ARBITER demo.

    Each agent:
    - Has its own HLC clock (agents don't share clocks — that's the point)
    - Is primarily bound to one transport (but can use others)
    - Emits events through transport adapters → bus
    - Has a unique agent_id for tracking
    """

    def __init__(
        self,
        agent_id: str,
        bus: EventBus,
        primary_transport: TransportType = "grpc",
        physical_clock: callable = None,
    ):
        """Initialize a toy agent.

        Args:
            agent_id: Unique identifier for this agent.
            bus: The shared event bus.
            primary_transport: The transport this agent primarily uses.
            physical_clock: Injectable physical clock for simulating skew.
        """
        self.agent_id = agent_id
        self.bus = bus
        self.primary_transport = primary_transport
        self.clock = HLCClock(physical_clock=physical_clock)
        self.capability_token: str | None = None

        # Transport adapters (all agents have access to all transports)
        self.transports: dict[str, Any] = {
            "grpc": GrpcSimTransport(bus),
            "queue": QueueSimTransport(bus),
            "blackboard": BlackboardSimTransport(bus),
            "webhook": WebhookSimTransport(bus),
            "stdout": StdoutSimTransport(bus),
        }

        self._running = False
        logger.info("Agent %s initialized (primary transport: %s)", agent_id, primary_transport)

    def get_transport(self, name: str | None = None) -> Any:
        """Get a transport adapter by name, defaulting to primary."""
        return self.transports[name or self.primary_transport]

    async def emit_event(
        self,
        kind: EventKind,
        resource_id: str | None = None,
        payload: dict[str, Any] | None = None,
        transport: TransportType | None = None,
        **kwargs,
    ) -> Event:
        """Emit an event through a transport adapter.

        Advances the agent's HLC clock and sends through the specified
        (or primary) transport, which in turn emits through the bus.

        Args:
            kind: Event kind.
            resource_id: Optional resource this event concerns.
            payload: Kind-specific data.
            transport: Transport to use (defaults to primary).
            **kwargs: Additional transport-specific arguments.

        Returns:
            The emitted Event with merged HLC timestamp.
        """
        hlc = self.clock.send_or_local_event()
        transport_name = transport or self.primary_transport
        adapter = self.transports[transport_name]

        if transport_name == "grpc":
            return await adapter.send(
                from_agent=self.agent_id,
                to_agent=kwargs.get("to_agent", "verifier"),
                hlc=hlc,
                kind=kind,
                resource_id=resource_id,
                payload=payload,
                capability_token=self.capability_token,
            )
        elif transport_name == "queue":
            return await adapter.publish(
                agent_id=self.agent_id,
                topic=kwargs.get("topic", f"tasks.{resource_id or 'general'}"),
                hlc=hlc,
                kind=kind,
                resource_id=resource_id,
                payload=payload,
                capability_token=self.capability_token,
            )
        elif transport_name == "blackboard":
            if payload and payload.get("op") == "read":
                _, event = await adapter.read(
                    agent_id=self.agent_id,
                    resource_id=resource_id or "unknown",
                    hlc=hlc,
                    capability_token=self.capability_token,
                )
                return event
            else:
                return await adapter.write(
                    agent_id=self.agent_id,
                    resource_id=resource_id or "unknown",
                    data=payload or {},
                    hlc=hlc,
                    kind=kind,
                    capability_token=self.capability_token,
                )
        elif transport_name == "webhook":
            return await adapter.post(
                from_agent=self.agent_id,
                to_url=kwargs.get("to_url", f"/webhook/{kwargs.get('to_agent', 'verifier')}"),
                hlc=hlc,
                kind=kind,
                resource_id=resource_id,
                payload=payload,
                capability_token=self.capability_token,
            )
        elif transport_name == "stdout":
            return await adapter.emit(
                agent_id=self.agent_id,
                hlc=hlc,
                kind=kind,
                message=kwargs.get("message", f"{kind} on {resource_id}"),
                resource_id=resource_id,
                payload=payload,
                capability_token=self.capability_token,
            )
        else:
            raise ValueError(f"Unknown transport: {transport_name}")

    async def receive_event(self, event: Event) -> None:
        """Process a received event by merging its HLC with ours.

        Called when this agent receives an event from another agent.
        This is the receive-side HLC merge that maintains causal ordering.
        """
        self.clock.on_receive(event.hlc)
        logger.debug(
            "Agent %s received event %s from %s",
            self.agent_id, event.kind, event.agent_id,
        )

    async def run(self) -> None:
        """Run the agent's behavior loop. Override in subclasses."""
        raise NotImplementedError("Subclasses must implement run()")

    async def start(self) -> None:
        """Start the agent's behavior loop."""
        self._running = True
        logger.info("Agent %s starting", self.agent_id)
        try:
            await self.run()
        finally:
            self._running = False
            logger.info("Agent %s stopped", self.agent_id)

    def stop(self) -> None:
        """Signal the agent to stop."""
        self._running = False
