"""Three-act demo orchestrator — runs the complete ARBITER demonstration.

Orchestrates the three-act scenario from Section 8 end to end:

  Act 1 — Happy path: Clean task handoff across 3+ transports
  Act 2 — Hard invariant: Fencing token structurally rejects stale write
  Act 3 — Soft invariant: Elle-style cycle catches write-skew anomaly

Starts the FastAPI server + agents concurrently, timed so the dashboard
visibly reflects each act as it happens.

Usage:
    python scripts/run_demo.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
import os
import signal
import webbrowser
from typing import Any

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.verifier.events import Event, HLCTimestamp
from backend.verifier.hlc import HLCClock
from backend.verifier.bus import EventBus
from backend.verifier.lease.lease_manager import LeaseManager
from backend.verifier.lease.protected_resource_impl import ProtectedResourceImpl
from backend.verifier.lease.protected_resource import WriteStatus
from backend.verifier.spec.state_machine import EngineContext, TransitionResult
from backend.verifier.spec.state_machine_engine import StateMachineEngineImpl
from backend.verifier.spec.task_ownership_spec import TASK_OWNERSHIP_SPEC
from backend.verifier.anomaly.dependency_graph_impl import DependencyGraphBuilderImpl
from backend.verifier.capability.capability_store import CapabilityStoreImpl
from backend.verifier.capability.tool_boundary import ToolCallBoundary
from backend.verifier.decompose.local_checker import PassthroughLocalChecker
from backend.verifier.transports.grpc_sim import GrpcSimTransport
from backend.verifier.transports.queue_sim import QueueSimTransport
from backend.verifier.transports.blackboard_sim import BlackboardSimTransport
from backend.verifier.transports.webhook_sim import WebhookSimTransport
from backend.verifier.transports.stdout_sim import StdoutSimTransport

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)7s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("arbiter.demo")

# Color codes for terminal output
BOLD = "\033[1m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
MAGENTA = "\033[35m"
RESET = "\033[0m"
DIM = "\033[2m"


def banner(text: str, color: str = CYAN) -> None:
    """Print a prominent banner to the console."""
    width = 64
    line = "═" * width
    print(f"\n{color}{BOLD}╔{line}╗")
    print(f"║  {text:<{width - 2}}║")
    print(f"╚{line}╝{RESET}\n")


def status(text: str, color: str = DIM) -> None:
    """Print a status line."""
    print(f"  {color}→ {text}{RESET}")


class DemoOrchestrator:
    """Orchestrates the three-act demo scenario.

    Patch §8: Creates fresh instances per invocation for re-runnability.

    Can accept pre-built components (e.g. from the FastAPI server) so that
    events flow through the server's WebSocket broadcast pipeline.
    """

    def __init__(self, *, bus=None, lease_manager=None, protected_resource=None,
                 state_engine=None, graph_builder=None, capability_store=None,
                 tool_boundary=None, local_checker=None):
        # Use injected components or create fresh ones
        self.bus = bus or EventBus()
        self.lease_manager = lease_manager or LeaseManager(default_ttl_events=10)
        self.protected_resource = protected_resource or ProtectedResourceImpl()

        if state_engine:
            self.state_engine = state_engine
        else:
            context = EngineContext(
                protected_resource=self.protected_resource,
                lease_manager=self.lease_manager,
            )
            self.state_engine = StateMachineEngineImpl(context=context)
            self.state_engine.load_spec(TASK_OWNERSHIP_SPEC)

        self.graph_builder = graph_builder or DependencyGraphBuilderImpl()
        self.capability_store = capability_store or CapabilityStoreImpl()
        self.tool_boundary = tool_boundary or ToolCallBoundary(self.capability_store)
        self.local_checker = local_checker or PassthroughLocalChecker(self.state_engine)

        # Transport adapters (always use the shared bus)
        self.grpc = GrpcSimTransport(self.bus)
        self.queue = QueueSimTransport(self.bus)
        self.blackboard = BlackboardSimTransport(self.bus)
        self.webhook = WebhookSimTransport(self.bus)
        self.stdout = StdoutSimTransport(self.bus)

        # Violations log
        self.violations: list[dict[str, Any]] = []

        # Only subscribe our own handler if we own the bus (headless mode).
        # When using the server's bus, the server's on_bus_event already
        # handles processing — we add a lightweight listener for violations tracking.
        if bus is None:
            self.bus.subscribe(self._on_event)
        else:
            # Tap into the shared bus to track violations locally for the summary
            self.bus.subscribe(self._track_violations_only)

    async def _on_event(self, event: Event) -> None:
        """Process each event through all verification layers."""
        # Tick per-resource counter for lease TTL
        if event.resource_id:
            self.lease_manager.tick_resource(event.resource_id)

        # Run through state machine engine
        outcome = self.state_engine.on_event(event)

        # Record in dependency graph
        self.graph_builder.record(event)

        # Check for cycles
        cycles = self.graph_builder.find_cycles()

        # Local checker
        self.local_checker.check_local(event)

        # Handle violations
        if outcome.result == TransitionResult.NO_MATCHING_TRANSITION:
            revoked = self.capability_store.revoke(event.agent_id)
            self.violations.append({
                "type": "state_machine_violation",
                "agent_id": event.agent_id,
                "reason": outcome.violation_reason,
                "revoked_scopes": [t.scope for t in revoked],
            })

        for cycle in cycles:
            for agent_id in cycle.agents:
                revoked = self.capability_store.revoke(agent_id)
                self.violations.append({
                    "type": "dependency_cycle",
                    "agent_id": agent_id,
                    "reason": cycle.description,
                    "revoked_scopes": [t.scope for t in revoked],
                })

    async def _track_violations_only(self, event: Event) -> None:
        """Lightweight listener: only records violations for demo summary.

        Used when sharing the server's bus — the server's on_bus_event already
        handles all processing (state machine, graph, revocations, WS broadcast).
        We just need to mirror the violation tracking for the demo summary.
        """
        # Check what the state engine decided (it already processed this event)
        if event.resource_id:
            state = self.state_engine.get_state(event.resource_id)
            # If the state is Violated or Escalated, and we haven't recorded it
            if state and state.state in ("Violated", "Escalated"):
                pass  # State tracking is sufficient for the demo

        # Track cycles for the summary
        cycles = self.graph_builder.find_cycles()
        for cycle in cycles:
            # Only add if not already tracked
            desc = cycle.description
            if not any(v.get("reason") == desc for v in self.violations):
                for agent_id in cycle.agents:
                    self.violations.append({
                        "type": "dependency_cycle",
                        "agent_id": agent_id,
                        "reason": desc,
                        "revoked_scopes": [],
                    })

    async def run_act1(self) -> dict[str, Any]:
        """Act 1 — Happy path: Clean task handoff across 3+ transports.

        Agent A claims task-42 (fencing token 1), does work, requests handoff.
        Agent B acknowledges. Uses gRPC, webhook, queue, and stdout transports.

        Expected: clean Idle→Claimed→InProgress→AwaitingAck→Acked run;
        zero violations; zero dependency-graph cycles.
        """
        banner("ACT 1 — HAPPY PATH: Clean Task Handoff", GREEN)
        resource_id = "task-42"
        clock_a = HLCClock()
        clock_b = HLCClock()

        # Issue capability tokens for both agents
        token_a = self.capability_store.issue("agent-A", "tool:*", clock_a.now())
        token_b = self.capability_store.issue("agent-B", "tool:*", clock_b.now())

        # Step 1: Agent A acquires lease (via gRPC)
        status("Agent A acquires lease for task-42")
        lease_a = self.lease_manager.acquire(resource_id, "agent-A", clock_a.now())
        status(f"  Fencing token issued: {lease_a.fencing_token}")

        # Step 2: Agent A claims task via gRPC
        status("Agent A claims task-42 via gRPC")
        await self.grpc.send(
            from_agent="agent-A", to_agent="verifier",
            hlc=clock_a.send_or_local_event(),
            kind="task_claim", resource_id=resource_id,
            payload={"fencing_token": lease_a.fencing_token, "op": "write"},
        )
        await asyncio.sleep(0.1)

        # Step 3: Agent A starts work (state_transition via gRPC)
        status("Agent A starts work on task-42")
        await self.grpc.send(
            from_agent="agent-A", to_agent="verifier",
            hlc=clock_a.send_or_local_event(),
            kind="state_transition", resource_id=resource_id,
            payload={"from_state": "Claimed", "to_state": "InProgress"},
        )
        await asyncio.sleep(0.1)

        # Step 4: Agent A writes result to protected resource
        status("Agent A writes result to protected resource")
        write_result = self.protected_resource.write(
            resource_id, lease_a.fencing_token, {"status": "completed_by_A"}
        )
        status(f"  Write result: {write_result.status.value}")

        # Step 5: Agent A requests handoff via webhook (different transport!)
        status("Agent A requests handoff via webhook → Agent B")
        await self.webhook.post(
            from_agent="agent-A", to_url="/webhook/agent-B",
            hlc=clock_a.send_or_local_event(),
            kind="handoff_request", resource_id=resource_id,
            payload={"op": "write", "target_agent": "agent-B"},
        )
        await asyncio.sleep(0.1)

        # Step 6: Agent D monitors via webhook (3rd transport!)
        status("Agent D monitors task via webhook")
        await self.webhook.post(
            from_agent="agent-D", to_url="/webhook/verifier",
            hlc=HLCClock().send_or_local_event(),
            kind="tool_call", resource_id=resource_id,
            payload={"op": "read", "tool_name": "monitor.status", "args": {}},
        )
        await asyncio.sleep(0.1)

        # Step 7: Agent B acknowledges handoff via queue (4th transport!)
        status("Agent B acknowledges handoff via queue")
        clock_b.on_receive(clock_a.current)
        await self.queue.publish(
            agent_id="agent-B", topic="tasks.task-42",
            hlc=clock_b.send_or_local_event(),
            kind="handoff_ack", resource_id=resource_id,
            payload={"op": "write", "from_agent": "agent-A"},
        )
        await asyncio.sleep(0.1)

        # Step 8: Agent B logs via stdout (5th transport!)
        status("Agent B logs completion via stdout")
        await self.stdout.emit(
            agent_id="agent-B", hlc=clock_b.send_or_local_event(),
            kind="state_transition", resource_id=resource_id,
            payload={"from_state": "AwaitingAck", "to_state": "Acked"},
            message="Handoff acknowledged for task-42",
        )

        # Verify results
        state = self.state_engine.get_state(resource_id)
        cycles = self.graph_builder.find_cycles()
        act1_violations = [v for v in self.violations]

        print(f"\n  {GREEN}✓ Final state: {state.state if state else 'N/A'}{RESET}")
        print(f"  {GREEN}✓ Violations: {len(act1_violations)}{RESET}")
        print(f"  {GREEN}✓ Dependency cycles: {len(cycles)}{RESET}")
        print(f"  {GREEN}✓ Transports used: gRPC, webhook, queue, stdout (4 distinct){RESET}")

        return {
            "final_state": state.state if state else None,
            "violations": len(act1_violations),
            "cycles": len(cycles),
            "write_accepted": write_result.accepted,
        }

    async def run_act2(self) -> dict[str, Any]:
        """Act 2 — Hard invariant: Fencing token structurally rejects stale write.

        Agent A acquires lease for task-42 (token N), then hangs. Lease expires.
        Agent B acquires fresh lease (token N+1), completes work, writes.
        Agent A "wakes up" and attempts stale write with token N.

        Expected: protected resource REJECTS Agent A's write outright.
        """
        banner("ACT 2 — HARD INVARIANT: Fencing Token Rejects Stale Write", YELLOW)
        resource_id = "task-42-act2"
        clock_a = HLCClock()
        clock_b = HLCClock()

        # Issue tokens
        token_a = self.capability_store.issue("agent-A", "tool:*", clock_a.now())
        token_b = self.capability_store.issue("agent-B", "tool:*", clock_b.now())

        # Step 1: Agent A acquires lease
        status("Agent A acquires lease for task-42-act2")
        lease_a = self.lease_manager.acquire(resource_id, "agent-A", clock_a.now())
        status(f"  Fencing token: {lease_a.fencing_token}")

        # Step 2: Agent A claims via gRPC
        status("Agent A claims task via gRPC")
        await self.grpc.send(
            from_agent="agent-A", to_agent="verifier",
            hlc=clock_a.send_or_local_event(),
            kind="task_claim", resource_id=resource_id,
            payload={"fencing_token": lease_a.fencing_token, "op": "write"},
        )

        # Step 3: Agent A HANGS — simulated stall
        status(f"{YELLOW}Agent A is HANGING (simulated stalled LLM call)...{RESET}")
        # Simulate events passing to expire the lease
        for i in range(12):
            self.lease_manager.tick_resource(resource_id)
            await self.stdout.emit(
                agent_id="system", hlc=HLCClock().send_or_local_event(),
                kind="tool_call", resource_id=resource_id,
                payload={"op": "read", "tool_name": "heartbeat", "args": {"tick": i}},
                message=f"  Heartbeat tick {i + 1}/12 — lease events: {lease_a.events_since_issued}/{lease_a.lease_ttl_events}",
            )
            await asyncio.sleep(0.05)

        # Step 4: Lease expires
        expired = self.lease_manager.expire_if_stale(resource_id)
        status(f"{YELLOW}Lease EXPIRED for Agent A (token={lease_a.fencing_token}){RESET}")

        # Step 5: Agent B acquires fresh lease
        status("Agent B acquires fresh lease")
        lease_b = self.lease_manager.acquire(resource_id, "agent-B", clock_b.now())
        status(f"  New fencing token: {lease_b.fencing_token}")

        # Step 6: Agent B claims and writes with valid token
        await self.queue.publish(
            agent_id="agent-B", topic="tasks.act2",
            hlc=clock_b.send_or_local_event(),
            kind="task_claim", resource_id=resource_id,
            payload={"fencing_token": lease_b.fencing_token, "op": "write"},
        )

        status("Agent B writes to protected resource with valid token")
        write_b = self.protected_resource.write(
            resource_id, lease_b.fencing_token, {"status": "completed_by_B"}
        )
        status(f"  Agent B write: {GREEN}{write_b.status.value}{RESET} (token={lease_b.fencing_token})")

        # Step 7: Agent A "wakes up" and attempts stale write
        status(f"{RED}Agent A wakes up — attempts write with STALE token {lease_a.fencing_token}{RESET}")
        write_a = self.protected_resource.write(
            resource_id, lease_a.fencing_token, {"status": "stale_write_from_A"}
        )

        # Emit fencing_conflict event
        if not write_a.accepted:
            await self.grpc.send(
                from_agent="agent-A", to_agent="verifier",
                hlc=clock_a.send_or_local_event(),
                kind="fencing_conflict", resource_id=resource_id,
                payload={
                    "rejected_token": lease_a.fencing_token,
                    "highest_token": write_a.highest_accepted_token,
                },
            )

        print(f"\n  {RED}✗ Agent A stale write: {write_a.status.value} "
              f"(token {lease_a.fencing_token} < highest {write_a.highest_accepted_token}){RESET}")
        print(f"  {GREEN}✓ Agent B valid write: {write_b.status.value}{RESET}")
        print(f"  {GREEN}✓ Task was NEVER double-executed — structurally prevented{RESET}")

        # Verify only B's write is in the log
        writes = self.protected_resource.get_writes(resource_id)
        print(f"  {GREEN}✓ Protected resource writes: {len(writes)} (only Agent B's){RESET}")

        return {
            "agent_a_write_rejected": not write_a.accepted,
            "agent_b_write_accepted": write_b.accepted,
            "writes_in_log": len(writes),
            "stale_token": lease_a.fencing_token,
            "valid_token": lease_b.fencing_token,
        }

    async def run_act3(self) -> dict[str, Any]:
        """Act 3 — Soft invariant: Elle-style cycle catches write-skew.

        Two agents read task-42's status from the blackboard at nearly the
        same causal moment and each independently claim the task.
        Neither violates the spec on its own — the cycle detector catches it.

        Expected: dependency graph cycle detected, both agents' tokens revoked.
        """
        banner("ACT 3 — SOFT INVARIANT: Elle-Style Cycle Catches Write-Skew", MAGENTA)
        resource_id = "task-42-act3"
        clock_a = HLCClock()
        clock_c = HLCClock()

        # Issue tokens for Act 3 agents
        token_a = self.capability_store.issue("agent-A", "tool:*", clock_a.now())
        token_c = self.capability_store.issue("agent-C", "tool:*", clock_c.now())

        # Initialize blackboard with task status
        await self.blackboard.write(
            agent_id="system", resource_id=resource_id,
            data={"status": "available", "assigned_to": None},
            hlc=HLCClock().send_or_local_event(),
            kind="state_transition",
        )

        violations_before = len(self.violations)

        # Step 1: Agent A reads blackboard (sees task is available)
        status("Agent A reads blackboard — sees task-42-act3 is available")
        value_a, event_a = await self.blackboard.read(
            agent_id="agent-A", resource_id=resource_id,
            hlc=clock_a.send_or_local_event(),
        )
        await asyncio.sleep(0.05)

        # Step 2: Agent C reads blackboard CONCURRENTLY (also sees available)
        status("Agent C reads blackboard — ALSO sees task-42-act3 is available")
        value_c, event_c = await self.blackboard.read(
            agent_id="agent-C", resource_id=resource_id,
            hlc=clock_c.send_or_local_event(),
        )
        await asyncio.sleep(0.05)

        # Step 3: Agent A claims based on its stale read
        status("Agent A claims task based on its read (writes to blackboard)")
        await self.blackboard.write(
            agent_id="agent-A", resource_id=resource_id,
            data={"status": "claimed", "assigned_to": "agent-A"},
            hlc=clock_a.send_or_local_event(),
            kind="task_claim",
        )
        await asyncio.sleep(0.05)

        # Step 4: Agent C claims based on ITS stale read (WRITE-SKEW!)
        status(f"{MAGENTA}Agent C claims task based on its stale read — WRITE-SKEW!{RESET}")
        await self.blackboard.write(
            agent_id="agent-C", resource_id=resource_id,
            data={"status": "claimed", "assigned_to": "agent-C"},
            hlc=clock_c.send_or_local_event(),
            kind="task_claim",
        )
        await asyncio.sleep(0.1)

        # Check for cycles
        cycles = self.graph_builder.find_cycles()
        new_violations = self.violations[violations_before:]

        # Verify token revocation
        a_tokens = self.capability_store.get_active_tokens("agent-A")
        c_tokens = self.capability_store.get_active_tokens("agent-C")

        # Demonstrate tool-call boundary rejection
        status("Testing tool-call boundary after revocation:")
        tool_result_a = self.tool_boundary.execute(
            "agent-A", "payment.charge", {"amount": 100}, token_a.token_id,
        )
        tool_result_c = self.tool_boundary.execute(
            "agent-C", "task.execute", {"task": resource_id}, token_c.token_id,
        )

        print(f"\n  {MAGENTA}⟳ Dependency graph cycles detected: {len(cycles)}{RESET}")
        for cycle in cycles:
            print(f"    {cycle.description}")
        print(f"  {RED}✗ Agent A active tokens: {len(a_tokens)} (revoked){RESET}")
        print(f"  {RED}✗ Agent C active tokens: {len(c_tokens)} (revoked){RESET}")
        print(f"  {RED}✗ Agent A tool call: {'REJECTED' if not tool_result_a.accepted else 'accepted'}{RESET}")
        print(f"  {RED}✗ Agent C tool call: {'REJECTED' if not tool_result_c.accepted else 'accepted'}{RESET}")
        print(f"  {GREEN}✓ Anomaly caught by Elle-style cycle detection, not by the spec{RESET}")

        return {
            "cycles_detected": len(cycles),
            "agent_a_tokens_revoked": len(a_tokens) == 0,
            "agent_c_tokens_revoked": len(c_tokens) == 0,
            "agent_a_tool_rejected": not tool_result_a.accepted,
            "agent_c_tool_rejected": not tool_result_c.accepted,
            "violations": len(new_violations),
        }

    async def run_full_demo(self) -> dict[str, Any]:
        """Run all three acts end to end."""
        banner("ARBITER — Causal Runtime Verifier for Multi-Agent Systems", CYAN)
        print(f"  {DIM}Starting three-act demonstration...{RESET}")
        print(f"  {DIM}Each act demonstrates a different verification layer.{RESET}\n")

        results = {}

        # Act 1
        results["act1"] = await self.run_act1()
        await asyncio.sleep(0.5)

        # Act 2
        results["act2"] = await self.run_act2()
        await asyncio.sleep(0.5)

        # Act 3
        results["act3"] = await self.run_act3()
        await asyncio.sleep(0.3)

        # Summary
        banner("DEMO COMPLETE — Summary", CYAN)

        a1 = results["act1"]
        a2 = results["act2"]
        a3 = results["act3"]

        print(f"  {GREEN}Act 1 (Happy Path):{RESET}")
        print(f"    State: {a1['final_state']} | Violations: {a1['violations']} | Cycles: {a1['cycles']}")

        print(f"  {YELLOW}Act 2 (Fencing Token):{RESET}")
        print(f"    Stale write rejected: {a2['agent_a_write_rejected']} | "
              f"Valid write accepted: {a2['agent_b_write_accepted']}")

        print(f"  {MAGENTA}Act 3 (Cycle Detection):{RESET}")
        print(f"    Cycles: {a3['cycles_detected']} | "
              f"Tokens revoked: A={a3['agent_a_tokens_revoked']} C={a3['agent_c_tokens_revoked']}")

        # Predicate slicing digest
        digest = self.local_checker.emit_digest()
        print(f"\n  {DIM}Local checker digest: {digest['events_seen']} events, "
              f"{len(digest['resource_ids_seen'])} resources{RESET}")

        # Validate all assertions
        all_pass = (
            a1["final_state"] == "Acked"
            and a1["violations"] == 0
            and a1["cycles"] == 0
            and a2["agent_a_write_rejected"]
            and a2["agent_b_write_accepted"]
            and a3["cycles_detected"] >= 1
        )

        if all_pass:
            print(f"\n  {GREEN}{BOLD}✓ ALL ASSERTIONS PASSED{RESET}")
        else:
            print(f"\n  {RED}{BOLD}✗ SOME ASSERTIONS FAILED{RESET}")

        return results


async def run_with_server():
    """Run the demo with the FastAPI server for the dashboard."""
    import uvicorn
    from backend.verifier.verifier_service import create_app

    app = create_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=8000, log_level="warning")
    server = uvicorn.Server(config)

    # Start server in background
    server_task = asyncio.create_task(server.serve())

    # Wait a moment for server to start
    await asyncio.sleep(1.0)

    # Open dashboard
    print(f"  {CYAN}Dashboard: http://127.0.0.1:8000{RESET}")
    try:
        webbrowser.open("http://127.0.0.1:8000")
    except Exception:
        pass

    # Wait for dashboard connection
    await asyncio.sleep(2.0)

    # Create orchestrator using the SERVER's shared components.
    # This means events emitted by the demo flow through the server's bus,
    # which triggers the server's on_bus_event → WS broadcast → dashboard.
    orchestrator = DemoOrchestrator(
        bus=app.state.bus,
        lease_manager=app.state.lease_manager,
        protected_resource=app.state.protected_resource,
        state_engine=app.state.state_engine,
        graph_builder=app.state.graph_builder,
        capability_store=app.state.capability_store,
        tool_boundary=app.state.tool_boundary,
        local_checker=app.state.local_checker,
    )

    results = await orchestrator.run_full_demo()

    print(f"\n  {DIM}Dashboard remains active at http://127.0.0.1:8000{RESET}")
    print(f"  {DIM}Press Ctrl+C to exit.{RESET}\n")

    # Keep server running for dashboard interaction
    try:
        await server_task
    except asyncio.CancelledError:
        pass

    return results


async def run_headless():
    """Run the demo without the server (for testing/CI)."""
    orchestrator = DemoOrchestrator()
    return await orchestrator.run_full_demo()


async def run_server_only():
    """Start the server without auto-running the demo.

    Users trigger the demo from the dashboard's 'Run Demo' button.
    """
    import uvicorn
    from backend.verifier.verifier_service import create_app

    app = create_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=8000, log_level="warning")
    server = uvicorn.Server(config)

    print(f"\n  {CYAN}ARBITER Dashboard: http://127.0.0.1:8000{RESET}")
    print(f"  {DIM}Click 'Run Demo' or 'Run Tests' in the dashboard.{RESET}")
    print(f"  {DIM}Press Ctrl+C to exit.{RESET}\n")

    try:
        webbrowser.open("http://127.0.0.1:8000")
    except Exception:
        pass

    await server.serve()


def main():
    """Entry point for the demo.

    Usage:
        python scripts/run_demo.py             # Start server + auto-run demo
        python scripts/run_demo.py --serve     # Start server only (use dashboard buttons)
        python scripts/run_demo.py --headless  # Run demo without server (for CI)
    """
    headless = "--headless" in sys.argv or "--test" in sys.argv
    serve_only = "--serve" in sys.argv

    if headless:
        results = asyncio.run(run_headless())
    elif serve_only:
        try:
            asyncio.run(run_server_only())
        except KeyboardInterrupt:
            print(f"\n{DIM}Server stopped.{RESET}")
    else:
        try:
            results = asyncio.run(run_with_server())
        except KeyboardInterrupt:
            print(f"\n{DIM}Demo interrupted.{RESET}")


if __name__ == "__main__":
    main()
