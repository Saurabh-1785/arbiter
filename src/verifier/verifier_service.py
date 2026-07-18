"""Verifier service — FastAPI application wiring all ARBITER components.

Provides:
- REST endpoints for state inspection
- WebSocket endpoint for live dashboard feed
- Typed WebSocket protocol with 4 message types (Patch §7):
  - event: new event from the bus
  - state_update: per-resource state machine status
  - graph_update: dependency graph nodes/edges
  - violation: detected violations and revocations
- Snapshot on connect: pushes current state to new WS clients (Patch §7)
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from src.verifier.events import Event, HLCTimestamp
from src.verifier.hlc import HLCClock
from src.verifier.bus import EventBus
from src.verifier.lease.lease_manager import LeaseManager
from src.verifier.lease.protected_resource_impl import ProtectedResourceImpl
from src.verifier.spec.state_machine import EngineContext, TransitionResult
from src.verifier.spec.state_machine_engine import StateMachineEngineImpl
from src.verifier.spec.task_ownership_spec import TASK_OWNERSHIP_SPEC
from src.verifier.anomaly.dependency_graph_impl import DependencyGraphBuilderImpl
from src.verifier.capability.capability_store import CapabilityStoreImpl
from src.verifier.capability.tool_boundary import ToolCallBoundary
from src.verifier.decompose.local_checker import PassthroughLocalChecker

logger = logging.getLogger(__name__)

# Dashboard static files directory
DASHBOARD_DIR = Path(__file__).parent.parent / "dashboard"


def create_app() -> FastAPI:
    """Create a fresh FastAPI app with all ARBITER components wired up.

    Patch §8: Creates fresh instances per invocation for re-runnability.
    """
    app = FastAPI(title="ARBITER — Causal Runtime Verifier", version="0.1.0")

    # -----------------------------------------------------------------------
    # Initialize all components (fresh instances — Patch §8)
    # -----------------------------------------------------------------------
    bus = EventBus()
    lease_manager = LeaseManager(default_ttl_events=10)
    protected_resource = ProtectedResourceImpl()
    context = EngineContext(
        protected_resource=protected_resource,
        lease_manager=lease_manager,
    )
    state_engine = StateMachineEngineImpl(context=context)
    state_engine.load_spec(TASK_OWNERSHIP_SPEC)
    graph_builder = DependencyGraphBuilderImpl()
    capability_store = CapabilityStoreImpl()
    tool_boundary = ToolCallBoundary(capability_store)
    local_checker = PassthroughLocalChecker(state_engine)

    # Store refs on app for access by the demo orchestrator
    app.state.bus = bus
    app.state.lease_manager = lease_manager
    app.state.protected_resource = protected_resource
    app.state.state_engine = state_engine
    app.state.graph_builder = graph_builder
    app.state.capability_store = capability_store
    app.state.tool_boundary = tool_boundary
    app.state.local_checker = local_checker

    # Active WebSocket connections
    ws_clients: list[WebSocket] = []
    violations_log: list[dict[str, Any]] = []

    # -----------------------------------------------------------------------
    # Bus subscriber: processes events through all engines + broadcasts to WS
    # -----------------------------------------------------------------------
    async def on_bus_event(event: Event) -> None:
        """Process each event through all verification layers and broadcast."""

        # 1. Tick per-resource counter for lease TTL
        if event.resource_id:
            lease_manager.tick_resource(event.resource_id)

        # 2. Run through state machine engine
        outcome = state_engine.on_event(event)

        # 3. Record in dependency graph
        graph_builder.record(event)

        # 4. Check for cycles (soft anomaly detection)
        cycles = graph_builder.find_cycles()

        # 5. Local checker pass
        local_checker.check_local(event)

        # 6. Handle violations — revoke tokens
        violation_data = None

        if outcome.result == TransitionResult.NO_MATCHING_TRANSITION:
            # State machine violation → revoke agent's tokens
            revoked = capability_store.revoke(event.agent_id)
            violation_data = {
                "type": "state_machine_violation",
                "agent_id": event.agent_id,
                "reason": outcome.violation_reason or "State machine violation",
                "revoked_scopes": [t.scope for t in revoked],
                "resource_id": event.resource_id,
            }
            violations_log.append(violation_data)

        if outcome.result == TransitionResult.TIMED_OUT:
            violation_data = {
                "type": "liveness_timeout",
                "agent_id": outcome.agent_id,
                "reason": outcome.violation_reason or "Liveness timeout",
                "revoked_scopes": [],
                "resource_id": event.resource_id,
            }
            violations_log.append(violation_data)

        for cycle in cycles:
            for agent_id in cycle.agents:
                revoked = capability_store.revoke(agent_id)
                cycle_violation = {
                    "type": "dependency_cycle",
                    "agent_id": agent_id,
                    "reason": cycle.description,
                    "revoked_scopes": [t.scope for t in revoked],
                    "resource_id": list(cycle.resource_ids)[0] if cycle.resource_ids else None,
                    "cycle": cycle.to_dict(),
                }
                violations_log.append(cycle_violation)

                if violation_data is None:
                    violation_data = cycle_violation

        # 7. Broadcast to WebSocket clients (Patch §7: typed messages)
        messages = [
            {"type": "event", "data": event.to_dict()},
        ]

        if event.resource_id:
            state = state_engine.get_state(event.resource_id)
            if state:
                messages.append({
                    "type": "state_update",
                    "data": state.to_dict(),
                })

        messages.append({
            "type": "graph_update",
            "data": graph_builder.to_serializable(),
        })

        if violation_data:
            messages.append({
                "type": "violation",
                "data": violation_data,
            })

        for msg in messages:
            await broadcast_ws(msg, ws_clients)

    bus.subscribe(on_bus_event)

    # -----------------------------------------------------------------------
    # WebSocket broadcast helper
    # -----------------------------------------------------------------------
    async def broadcast_ws(message: dict, clients: list[WebSocket]) -> None:
        """Send a message to all connected WebSocket clients."""
        disconnected = []
        text = json.dumps(message)
        for ws in clients:
            try:
                await ws.send_text(text)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            clients.remove(ws)

    # -----------------------------------------------------------------------
    # WebSocket endpoint
    # -----------------------------------------------------------------------
    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await websocket.accept()
        ws_clients.append(websocket)
        logger.info("WebSocket client connected (total: %d)", len(ws_clients))

        try:
            # Patch §7: Snapshot on connect — push current state
            # Send current state of all tracked resources
            for rid, instance in state_engine.get_all_states().items():
                await websocket.send_text(json.dumps({
                    "type": "state_update",
                    "data": instance.to_dict(),
                }))

            # Send current graph
            await websocket.send_text(json.dumps({
                "type": "graph_update",
                "data": graph_builder.to_serializable(),
            }))

            # Send all past violations
            for violation in violations_log:
                await websocket.send_text(json.dumps({
                    "type": "violation",
                    "data": violation,
                }))

            # Send all past events
            for event in bus.get_events():
                await websocket.send_text(json.dumps({
                    "type": "event",
                    "data": event.to_dict(),
                }))

            # Keep connection alive
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            if websocket in ws_clients:
                ws_clients.remove(websocket)
            logger.info("WebSocket client disconnected (total: %d)", len(ws_clients))

    # -----------------------------------------------------------------------
    # REST endpoints
    # -----------------------------------------------------------------------
    @app.get("/")
    async def dashboard():
        """Serve the dashboard HTML."""
        index_path = DASHBOARD_DIR / "index.html"
        if index_path.exists():
            return FileResponse(str(index_path))
        return HTMLResponse("<h1>ARBITER Dashboard</h1><p>Dashboard files not found.</p>")

    @app.get("/api/state/{resource_id}")
    async def get_state(resource_id: str):
        """Get current state machine state for a resource."""
        state = state_engine.get_state(resource_id)
        if state:
            return state.to_dict()
        return {"error": "Resource not found"}

    @app.get("/api/states")
    async def get_all_states():
        """Get all tracked resource states."""
        return {
            rid: inst.to_dict()
            for rid, inst in state_engine.get_all_states().items()
        }

    @app.get("/api/graph")
    async def get_graph():
        """Get the current dependency graph."""
        return graph_builder.to_serializable()

    @app.get("/api/violations")
    async def get_violations():
        """Get all detected violations."""
        return {"violations": violations_log}

    @app.get("/api/leases")
    async def get_leases():
        """Get active leases."""
        return {
            rid: {
                "owner": lease.owner_agent_id,
                "token": lease.fencing_token,
                "ttl_events": lease.lease_ttl_events,
                "events_since_issued": lease.events_since_issued,
                "expired": lease.is_expired(),
            }
            for rid, lease in lease_manager.get_all_leases().items()
        }

    @app.get("/api/tokens/{agent_id}")
    async def get_agent_tokens(agent_id: str):
        """Get tokens for an agent."""
        return {
            "tokens": [t.to_dict() for t in capability_store.get_agent_tokens(agent_id)],
        }

    @app.get("/api/events")
    async def get_events():
        """Get all events."""
        return {"events": [e.to_dict() for e in bus.get_events()]}

    @app.get("/api/digest")
    async def get_digest():
        """Get the local checker's digest (predicate slicing boundary)."""
        return local_checker.emit_digest()

    # Mount static files for dashboard (CSS/JS)
    if DASHBOARD_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(DASHBOARD_DIR)), name="static")

    return app


# Default app instance for uvicorn
app = create_app()
