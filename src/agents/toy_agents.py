"""Toy agents for the three-act demo scenario (Section 8).

Four concrete agents (A, B, C, D), each primarily bound to one transport:
  - Agent A: gRPC (the "normal" agent)
  - Agent B: queue/NATS (the "handoff receiver")
  - Agent C: blackboard (the "shared-state reader" — key to Act 3)
  - Agent D: webhook (the "webhook-based coordinator")

Each agent's run() method is parameterized by scenario to support all three acts:
  - Act 1: Happy path — clean task handoff
  - Act 2: Hard invariant — fencing token rejects stale write
  - Act 3: Soft invariant — write-skew cycle detection
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.agents.base_agent import BaseAgent
from src.verifier.bus import EventBus
from src.verifier.lease.fencing import FencingLease

logger = logging.getLogger(__name__)


class AgentA(BaseAgent):
    """Agent A — primarily uses gRPC.

    Act 1: Claims task-42, does work, requests handoff to B.
    Act 2: Claims task-42, then "hangs" (simulated stall), attempts stale write.
    Act 3: Reads blackboard, independently claims task-42 (write-skew).
    """

    def __init__(self, bus: EventBus, physical_clock: callable = None):
        super().__init__(
            agent_id="agent-A",
            bus=bus,
            primary_transport="grpc",
            physical_clock=physical_clock,
        )
        self.scenario: str = "act1"
        self.lease: FencingLease | None = None
        self.lease_manager: Any = None
        self.protected_resource: Any = None
        self.results: dict[str, Any] = {}

    async def run(self) -> None:
        """Execute the scripted behavior for the current scenario."""
        if self.scenario == "act1":
            await self._run_act1()
        elif self.scenario == "act2":
            await self._run_act2()
        elif self.scenario == "act3":
            await self._run_act3()

    async def _run_act1(self) -> None:
        """Act 1: Happy path — claim, work, handoff request."""
        resource_id = "task-42"

        # Acquire lease
        if self.lease_manager:
            self.lease = self.lease_manager.acquire(
                resource_id, self.agent_id, self.clock.now()
            )
            logger.info("Agent A acquired lease for %s (token=%d)", resource_id, self.lease.fencing_token)

        # Claim task via gRPC
        await self.emit_event(
            kind="task_claim",
            resource_id=resource_id,
            payload={"fencing_token": self.lease.fencing_token if self.lease else 1, "op": "write"},
            transport="grpc",
            to_agent="verifier",
        )
        await asyncio.sleep(0.05)

        # Start work (state_transition)
        await self.emit_event(
            kind="state_transition",
            resource_id=resource_id,
            payload={"from_state": "Claimed", "to_state": "InProgress"},
            transport="grpc",
            to_agent="verifier",
        )
        await asyncio.sleep(0.1)

        # Write result to protected resource
        if self.protected_resource and self.lease:
            result = self.protected_resource.write(
                resource_id, self.lease.fencing_token, {"status": "completed_by_A"}
            )
            self.results["write_result"] = result

        # Request handoff via webhook (using a different transport!)
        await self.emit_event(
            kind="handoff_request",
            resource_id=resource_id,
            payload={"op": "write", "target_agent": "agent-B"},
            transport="webhook",
            to_agent="agent-B",
        )

    async def _run_act2(self) -> None:
        """Act 2: Stale write — acquire lease, hang, attempt write after expiry."""
        resource_id = "task-42"

        # Acquire lease
        if self.lease_manager:
            self.lease = self.lease_manager.acquire(
                resource_id, self.agent_id, self.clock.now()
            )
            logger.info(
                "Agent A acquired lease for %s (token=%d)",
                resource_id, self.lease.fencing_token,
            )

        # Claim task
        await self.emit_event(
            kind="task_claim",
            resource_id=resource_id,
            payload={"fencing_token": self.lease.fencing_token if self.lease else 1, "op": "write"},
            transport="grpc",
            to_agent="verifier",
        )

        # Simulate hanging — wait for lease to expire
        # The orchestrator will advance per-resource event counts during this wait
        logger.info("Agent A is HANGING (simulated stall)...")
        await asyncio.sleep(0.3)

        # Attempt stale write with old fencing token
        if self.protected_resource and self.lease:
            result = self.protected_resource.write(
                resource_id, self.lease.fencing_token, {"status": "stale_write_from_A"}
            )
            self.results["stale_write_result"] = result
            logger.info(
                "Agent A stale write result: %s (token=%d)",
                result.status.value, self.lease.fencing_token,
            )

    async def _run_act3(self) -> None:
        """Act 3: Write-skew — read blackboard, independently claim."""
        resource_id = "task-42"

        # Read blackboard state (this creates a WR dependency)
        await self.emit_event(
            kind="tool_call",
            resource_id=resource_id,
            payload={"op": "read", "tool_name": "blackboard.read", "args": {}},
            transport="blackboard",
        )
        await asyncio.sleep(0.02)

        # Based on the read (which shows task is available), claim it
        await self.emit_event(
            kind="task_claim",
            resource_id=resource_id,
            payload={"fencing_token": 0, "op": "write"},
            transport="blackboard",
        )
        await asyncio.sleep(0.02)

        # Start work
        await self.emit_event(
            kind="state_transition",
            resource_id=resource_id,
            payload={"from_state": "Claimed", "to_state": "InProgress", "op": "write"},
            transport="blackboard",
        )


class AgentB(BaseAgent):
    """Agent B — primarily uses queue/NATS.

    Act 1: Receives handoff, acknowledges.
    Act 2: Acquires lease after A's expires, completes work, writes with new token.
    Act 3: Not directly involved (C handles the write-skew partner role).
    """

    def __init__(self, bus: EventBus, physical_clock: callable = None):
        super().__init__(
            agent_id="agent-B",
            bus=bus,
            primary_transport="queue",
            physical_clock=physical_clock,
        )
        self.scenario: str = "act1"
        self.lease: FencingLease | None = None
        self.lease_manager: Any = None
        self.protected_resource: Any = None
        self.results: dict[str, Any] = {}

    async def run(self) -> None:
        if self.scenario == "act1":
            await self._run_act1()
        elif self.scenario == "act2":
            await self._run_act2()
        elif self.scenario == "act3":
            await self._run_act3()

    async def _run_act1(self) -> None:
        """Act 1: Receive handoff request, acknowledge."""
        resource_id = "task-42"

        # Wait a bit for A to send the handoff request
        await asyncio.sleep(0.2)

        # Acknowledge handoff via queue
        await self.emit_event(
            kind="handoff_ack",
            resource_id=resource_id,
            payload={"op": "write", "from_agent": "agent-A"},
            transport="queue",
            topic="tasks.task-42",
        )

        # Log completion via stdout (using another transport)
        await self.emit_event(
            kind="state_transition",
            resource_id=resource_id,
            payload={"from_state": "AwaitingAck", "to_state": "Acked"},
            transport="stdout",
            message="Handoff acknowledged for task-42",
        )

    async def _run_act2(self) -> None:
        """Act 2: Acquire lease after A's expires, complete work, write with new token."""
        resource_id = "task-42"

        # Wait for A to hang and lease to expire
        await asyncio.sleep(0.15)

        # Expire A's lease and acquire our own
        if self.lease_manager:
            self.lease_manager.expire_if_stale(resource_id)
            self.lease = self.lease_manager.acquire(
                resource_id, self.agent_id, self.clock.now()
            )
            logger.info(
                "Agent B acquired lease for %s (token=%d)",
                resource_id, self.lease.fencing_token,
            )

        # Claim task
        await self.emit_event(
            kind="task_claim",
            resource_id=resource_id,
            payload={"fencing_token": self.lease.fencing_token if self.lease else 2, "op": "write"},
            transport="queue",
            topic="tasks.task-42",
        )
        await asyncio.sleep(0.05)

        # Do work and write with our valid token
        if self.protected_resource and self.lease:
            result = self.protected_resource.write(
                resource_id, self.lease.fencing_token, {"status": "completed_by_B"}
            )
            self.results["write_result"] = result
            logger.info(
                "Agent B write result: %s (token=%d)",
                result.status.value, self.lease.fencing_token,
            )

        # Report via stdout
        await self.emit_event(
            kind="state_transition",
            resource_id=resource_id,
            payload={"from_state": "Claimed", "to_state": "InProgress"},
            transport="stdout",
            message="Agent B completing task-42 with valid lease",
        )

    async def _run_act3(self) -> None:
        """Act 3: B is not the write-skew partner — just observe."""
        await asyncio.sleep(0.1)


class AgentC(BaseAgent):
    """Agent C — primarily uses blackboard.

    Act 1: Observes (no active role).
    Act 2: Observes (no active role).
    Act 3: The write-skew partner — reads blackboard concurrently with A,
           independently claims the same task. This creates the cycle.
    """

    def __init__(self, bus: EventBus, physical_clock: callable = None):
        super().__init__(
            agent_id="agent-C",
            bus=bus,
            primary_transport="blackboard",
            physical_clock=physical_clock,
        )
        self.scenario: str = "act1"
        self.results: dict[str, Any] = {}

    async def run(self) -> None:
        if self.scenario == "act1":
            await self._run_act1()
        elif self.scenario == "act2":
            await self._run_act2()
        elif self.scenario == "act3":
            await self._run_act3()

    async def _run_act1(self) -> None:
        """Act 1: Observe only."""
        await asyncio.sleep(0.1)
        # Log observation via stdout
        await self.emit_event(
            kind="state_transition",
            resource_id="task-42",
            payload={"from_state": "observing", "to_state": "observing"},
            transport="stdout",
            message="Agent C observing task-42 progress",
        )

    async def _run_act2(self) -> None:
        """Act 2: Observe only."""
        await asyncio.sleep(0.2)

    async def _run_act3(self) -> None:
        """Act 3: Write-skew — read blackboard concurrently with A, independently claim.

        This is the classic write-skew pattern:
        - A reads task status (available)
        - C reads task status (available) — CONCURRENTLY
        - A claims the task based on its read
        - C claims the task based on its read
        Both are individually valid, but together they violate the invariant.
        The dependency graph will catch the cycle.
        """
        resource_id = "task-42"

        # Read blackboard state (nearly concurrent with A's read)
        await asyncio.sleep(0.01)  # Slight offset for realistic simulation
        await self.emit_event(
            kind="tool_call",
            resource_id=resource_id,
            payload={"op": "read", "tool_name": "blackboard.read", "args": {}},
            transport="blackboard",
        )
        await asyncio.sleep(0.03)

        # Based on the read (which shows task is available), claim it
        await self.emit_event(
            kind="task_claim",
            resource_id=resource_id,
            payload={"fencing_token": 0, "op": "write"},
            transport="blackboard",
        )
        await asyncio.sleep(0.02)

        # Start work
        await self.emit_event(
            kind="state_transition",
            resource_id=resource_id,
            payload={"from_state": "Claimed", "to_state": "InProgress", "op": "write"},
            transport="blackboard",
        )


class AgentD(BaseAgent):
    """Agent D — primarily uses webhook.

    Acts as a coordinator/observer across all scenarios. Uses webhook
    transport to demonstrate the fourth transport type.
    """

    def __init__(self, bus: EventBus, physical_clock: callable = None):
        super().__init__(
            agent_id="agent-D",
            bus=bus,
            primary_transport="webhook",
            physical_clock=physical_clock,
        )
        self.scenario: str = "act1"
        self.results: dict[str, Any] = {}

    async def run(self) -> None:
        if self.scenario == "act1":
            await self._run_act1()
        elif self.scenario == "act2":
            await self._run_act2()
        elif self.scenario == "act3":
            await self._run_act3()

    async def _run_act1(self) -> None:
        """Act 1: Monitor and log the handoff via webhook."""
        resource_id = "task-42"
        await asyncio.sleep(0.15)

        # Report monitoring via webhook
        await self.emit_event(
            kind="tool_call",
            resource_id=resource_id,
            payload={
                "op": "read",
                "tool_name": "monitor.status",
                "args": {"resource_id": resource_id},
            },
            transport="webhook",
            to_agent="verifier",
        )

    async def _run_act2(self) -> None:
        """Act 2: Observe the fencing conflict."""
        await asyncio.sleep(0.35)

        await self.emit_event(
            kind="tool_call",
            resource_id="task-42",
            payload={
                "op": "read",
                "tool_name": "monitor.fencing_status",
                "args": {"resource_id": "task-42"},
            },
            transport="webhook",
            to_agent="verifier",
        )

    async def _run_act3(self) -> None:
        """Act 3: Observe the write-skew detection."""
        await asyncio.sleep(0.08)

        await self.emit_event(
            kind="tool_call",
            resource_id="task-42",
            payload={
                "op": "read",
                "tool_name": "monitor.anomaly_status",
                "args": {"resource_id": "task-42"},
            },
            transport="webhook",
            to_agent="verifier",
        )
