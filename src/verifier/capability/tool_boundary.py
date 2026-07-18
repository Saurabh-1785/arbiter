"""Tool-call boundary — the circuit breaker that gates agent actions.

Every simulated tool call passes through this boundary. It checks the
agent's capability token against the CapabilityStore and rejects any
call with a revoked token.

The key insight: you can't cleanly interrupt an LLM mid-generation,
so you gate the ACTION, not the PROCESS. The agent can keep "thinking"
all it wants; its next real-world action is rejected here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src.verifier.capability.capability_store import CapabilityStoreImpl

logger = logging.getLogger(__name__)


@dataclass
class ToolCallResult:
    """Result of a tool call attempt at the boundary."""
    accepted: bool
    tool_name: str
    agent_id: str
    result: Any = None           # tool result if accepted
    rejection_reason: str = ""   # reason if rejected

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "tool_name": self.tool_name,
            "agent_id": self.agent_id,
            "result": str(self.result) if self.result else None,
            "rejection_reason": self.rejection_reason,
        }


class ToolCallBoundary:
    """The circuit breaker: gates every agent tool call through capability check.

    Every simulated tool call must pass through execute(). If the agent's
    capability token has been revoked, the call is rejected — the agent's
    other unrelated processing is unaffected (we're gating the action,
    not killing the process).
    """

    def __init__(self, capability_store: CapabilityStoreImpl):
        self._store = capability_store
        self._call_log: list[ToolCallResult] = []

    def execute(
        self,
        agent_id: str,
        tool_name: str,
        args: dict[str, Any],
        capability_token: str | None,
    ) -> ToolCallResult:
        """Attempt a tool call, checking the capability token.

        Args:
            agent_id: The agent making the call.
            tool_name: The tool being called.
            args: Arguments to the tool.
            capability_token: The token presented with the call.

        Returns:
            ToolCallResult indicating whether the call was accepted or rejected.
        """
        # Check token validity
        if capability_token is None:
            result = ToolCallResult(
                accepted=False,
                tool_name=tool_name,
                agent_id=agent_id,
                rejection_reason="No capability token presented",
            )
            self._call_log.append(result)
            logger.warning(
                "TOOL CALL REJECTED: %s by %s — no token",
                tool_name, agent_id,
            )
            return result

        if not self._store.check(capability_token):
            result = ToolCallResult(
                accepted=False,
                tool_name=tool_name,
                agent_id=agent_id,
                rejection_reason=(
                    f"Capability token {capability_token[:8]}... has been REVOKED. "
                    f"Agent {agent_id} is barred from tool calls pending resolution."
                ),
            )
            self._call_log.append(result)
            logger.warning(
                "TOOL CALL REJECTED: %s by %s — token REVOKED",
                tool_name, agent_id,
            )
            return result

        # Token is valid — simulate tool execution
        simulated_result = {
            "tool": tool_name,
            "args": args,
            "status": "executed",
            "agent": agent_id,
        }

        result = ToolCallResult(
            accepted=True,
            tool_name=tool_name,
            agent_id=agent_id,
            result=simulated_result,
        )
        self._call_log.append(result)

        logger.info("Tool call accepted: %s by %s", tool_name, agent_id)
        return result

    def get_call_log(self) -> list[ToolCallResult]:
        """Return the full tool call log."""
        return list(self._call_log)

    def get_rejections(self) -> list[ToolCallResult]:
        """Return only rejected tool calls."""
        return [r for r in self._call_log if not r.accepted]

    def reset(self) -> None:
        """Clear call log for re-runnability."""
        self._call_log.clear()
