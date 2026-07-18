"""Task ownership handoff spec — the concrete demo invariant (Section 6.4).

This is the spec that drives the three-act demo. It's expressed as a Python
dict (equivalent to the YAML in Section 6.4) — data, not code.

This is a conjunctive/stable-predicate-shaped spec by construction (Section 3.1)
— the restriction to these two efficiently-detectable predicate classes is
the point, not a limitation.
"""

# The task ownership handoff spec — Section 6.4
TASK_OWNERSHIP_SPEC: dict = {
    "name": "task_ownership_handoff",
    "states": [
        "Idle",          # no agent holds this task
        "Claimed",       # one agent has claimed it (fencing token issued)
        "InProgress",    # the owning agent is actively working
        "AwaitingAck",   # handoff requested, waiting for acknowledgment
        "Acked",         # handoff acknowledged — terminal success state
        "Escalated",     # liveness timeout — AwaitingAck never resolved
        "Violated",      # safety violation detected
    ],
    "start": "Idle",
    "transitions": [
        # Happy path: Idle -> Claimed -> InProgress -> AwaitingAck -> Acked
        {
            "from": "Idle",
            "on": "task_claim",
            "to": "Claimed",
            "guard": "fencing_token_valid",
        },
        {
            "from": "Claimed",
            "on": "start_work",
            "to": "InProgress",
        },
        {
            "from": "InProgress",
            "on": "handoff_request",
            "to": "AwaitingAck",
        },
        {
            "from": "AwaitingAck",
            "on": "handoff_ack",
            "to": "Acked",
        },
        # Liveness: AwaitingAck must resolve within 20 events or escalate
        {
            "from": "AwaitingAck",
            "on": "timeout",
            "to": "Escalated",
            "after_events": 20,
        },
        # Release path: any claimed/in-progress state back to Idle
        {
            "from": "Claimed",
            "on": "task_release",
            "to": "Idle",
        },
        {
            "from": "InProgress",
            "on": "task_release",
            "to": "Idle",
        },
        # Safety: fencing conflict from any state -> Violated
        # The wildcard "*" means this transition matches from ANY state
        {
            "from": "*",
            "on": "fencing_conflict",
            "to": "Violated",
        },
    ],
    # Safety property — interpreted per-resource (Patch §4 / Patch §10):
    # for a given resource_id, at most one owning agent at a time.
    "safety": (
        "at most one resource_id may be in {Claimed, InProgress, AwaitingAck} "
        "across all agents at once (interpreted per-resource: one owning agent "
        "at a time per resource_id)"
    ),
    # Liveness property:
    "liveness": (
        "AwaitingAck must reach Acked or Escalated within after_events steps"
    ),
}
