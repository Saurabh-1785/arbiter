# ARBITER — Implementation Plan

> Restated from the build prompt's Section 7, with file-level detail and scoping decisions.

## Assumptions

1. **Python 3.11+** — using `@dataclass`, `from __future__ import annotations`, union syntax `X | Y`
2. **`@dataclass` over pydantic** — simpler, fewer dependencies, adequate for the demo
3. **In-memory only** — no database, no persistence (deliberate, Section 9)
4. **Event-count TTL** — not wall-clock, for deterministic demo behavior (Section 6.2)
5. **Per-resource counters** — lease TTL and `after_events` count per-resource, not global bus count
6. **Safety property per-resource** — "one owning agent per resource_id", not system-wide

## Phase 0 — Scaffold & Contracts (30–45 min)

**Objective:** Set up repo structure, all data contracts as importable dataclasses.

**Files created:**
- `pyproject.toml` — project config with `asyncio_mode = "auto"`
- `src/verifier/__init__.py` — package init
- `src/verifier/events.py` — `HLCTimestamp`, `Event` (with `fencing_conflict` kind, payload contracts)
- `src/verifier/lease/fencing.py` — `FencingLease`, `LeaseBackend` protocol, `InMemoryLeaseBackend`
- `src/verifier/lease/protected_resource.py` — `WriteResult`, `WriteStatus`, `ProtectedResourceProtocol`
- `src/verifier/capability/tokens.py` — `CapabilityToken`, `CapabilityStoreProtocol`
- `src/verifier/spec/state_machine.py` — `TransitionResult`, `TransitionOutcome`, `MachineInstance`, `EngineContext`, `GuardFn`
- `src/verifier/anomaly/dependency_graph.py` — `EdgeType`, `Cycle`, `DependencyGraphBuilderProtocol`
- `tests/test_placeholder.py` — schema import and construction tests
- All `__init__.py` files for package structure

**Definition of Done:** ✅ All schemas importable, placeholder tests pass

## Phase 1 — Causal Capture (2–3 hr)

**Objective:** HLC + toy agents + multi-transport bus

**Files created:**
- `src/verifier/hlc.py` — `HLCClock` with exact merge rules from Demirbas et al. 2014
- `src/verifier/bus.py` — `EventBus` with HLC-stamping, per-resource counters, pub/sub
- `src/verifier/transports/grpc_sim.py` — gRPC transport adapter
- `src/verifier/transports/queue_sim.py` — NATS/Kafka queue adapter
- `src/verifier/transports/blackboard_sim.py` — shared KV store adapter (reads emit `op="read"`)
- `src/verifier/transports/webhook_sim.py` — webhook adapter
- `src/verifier/transports/stdout_sim.py` — stdout adapter
- `src/agents/base_agent.py` — `BaseAgent` with own HLC clock
- `src/agents/toy_agents.py` — 4 concrete agents (A=gRPC, B=queue, C=blackboard, D=webhook)
- `tests/test_hlc.py` — monotonicity, merge, clock skew, shuffle-reconstruct

**Definition of Done:** ✅ HLC tests pass, 4 agents emit across ≥3 transports

## Phase 2 — Hard Invariant Prevention (1–2 hr)

**Objective:** Fencing-token lease + protected resource

**Files created:**
- `src/verifier/lease/lease_manager.py` — `LeaseManager` backed by `LeaseBackend`
- `src/verifier/lease/protected_resource_impl.py` — `ProtectedResourceImpl` with fencing enforcement
- `tests/test_fencing.py` — token monotonicity, stale rejection, TTL expiry, end-to-end

**Definition of Done:** ✅ Stale writes structurally rejected, not logged

## Phase 3 — Specification Layer (2–3 hr)

**Objective:** State-machine engine + task-ownership spec

**Files created:**
- `src/verifier/spec/state_machine_engine.py` — engine with guard registry, owner tracking, per-resource counters
- `src/verifier/spec/task_ownership_spec.py` — the concrete demo spec
- `tests/test_state_machine.py` — happy path, double-claim, timeout, fencing conflict

**Definition of Done:** ✅ Spec drives correct transitions, violations detected

## Phase 4 — Soft Invariant Detection (1.5–2 hr)

**Objective:** Dependency graph + cycle detector

**Files created:**
- `src/verifier/anomaly/dependency_graph_impl.py` — DSG builder with WW/WR/RW edges using networkx
- `tests/test_cycle_detector.py` — write-skew cycle, zero false positives, serializable output

**Definition of Done:** ✅ Cyclic history flagged, clean history = zero cycles

## Phase 5 — Circuit Breaker (1 hr)

**Objective:** Capability tokens + revocation

**Files created:**
- `src/verifier/capability/capability_store.py` — `CapabilityStoreImpl`
- `src/verifier/capability/tool_boundary.py` — `ToolCallBoundary`

**Definition of Done:** ✅ Revoked tokens rejected at boundary, unrelated processing unaffected

## Phase 6 — Dashboard & Demo Orchestration (3–4 hr)

**Objective:** Live visualization + three-act demo

**Files created:**
- `src/verifier/verifier_service.py` — FastAPI + WebSocket with typed protocol
- `src/dashboard/index.html` — 4-panel dashboard
- `src/dashboard/styles.css` — dark theme with glassmorphism
- `src/dashboard/app.js` — WebSocket client + D3.js graph
- `src/verifier/decompose/local_checker.py` — predicate-slicing boundary stub
- `scripts/run_demo.py` — three-act orchestrator
- `tests/test_demo_e2e.py` — end-to-end test

**Definition of Done:** ✅ Dashboard updates live, all three acts run with no manual steps

## Phase 7 — Judge-Facing Polish (1–1.5 hr)

**Files created:**
- `README.md` — problem statement, architecture, narrative, scoping decisions
- `IMPLEMENTATION_PLAN.md` — this file

**Definition of Done:** ✅ One-command demo, README explains every technique, no stubs on demo path

## Scoping Decisions

- Fencing-lease and `AwaitingAck` timeouts use **per-resource** event counts, not global bus count
- `expire_if_stale()` uses event-count integer, not `HLCTimestamp` — matches Section 6.2's "not wall-clock" requirement
- Blackboard reads modeled as `tool_call` events with `payload["op"] = "read"`, keeping Event.kind enum closed
- Safety property "at most one resource_id in {Claimed, InProgress, AwaitingAck}..." interpreted per-resource
- Transport adapters are thin Python wrappers — swap point for real eBPF capture
- `LeaseManager` delegates to `LeaseBackend` protocol — swap point for real etcd/Raft
- `PassthroughLocalChecker` is a ~30-line stub proving the predicate-slicing interface exists
- Fresh app instances per `run_demo.py` invocation for back-to-back re-runnability
