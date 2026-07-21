"""Capability token management — the circuit breaker for misbehaving agents.

Every agent receives short-lived, narrowly-scoped, revocable capability tokens
for each tool call. The moment the verifier (state-machine engine or cycle
detector) confirms a violation, it revokes that agent's token. The agent can
keep "thinking" all it wants; its next real-world action is rejected at the
tool-call boundary.

This is a known pattern in current agent-safety work — you can't cleanly
interrupt an LLM mid-generation, so you gate the action, not the process.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Protocol

from backend.verifier.events import HLCTimestamp


# ---------------------------------------------------------------------------
# CapabilityToken — Section 6.3
# ---------------------------------------------------------------------------

@dataclass
class CapabilityToken:
    """A short-lived, narrowly-scoped, revocable capability token.

    Scopes follow tool-name patterns: "tool:payment.charge", "tool:*", etc.
    Revocation is immediate and irreversible for a given token_id.
    """

    token_id: str              # uuid4
    agent_id: str              # which agent holds this token
    scope: str                 # e.g. "tool:payment.charge", "tool:*"
    issued_at: HLCTimestamp    # when issued (causal time)
    revoked: bool = False      # once True, permanently rejected

    def to_dict(self) -> dict:
        """Serialize for WebSocket transport."""
        return {
            "token_id": self.token_id,
            "agent_id": self.agent_id,
            "scope": self.scope,
            "issued_at": self.issued_at.to_dict(),
            "revoked": self.revoked,
        }


# ---------------------------------------------------------------------------
# CapabilityStore protocol — Section 6.5
# ---------------------------------------------------------------------------

class CapabilityStoreProtocol(Protocol):
    """Protocol for capability token issuance, revocation, and validation.

    Revocation can target a specific scope or all scopes for an agent.
    check() is called at the tool-call boundary for every agent action.
    """

    def issue(self, agent_id: str, scope: str, at: HLCTimestamp) -> CapabilityToken:
        """Issue a new capability token for an agent+scope pair."""
        ...

    def revoke(self, agent_id: str, scope: str | None = None) -> list[CapabilityToken]:
        """Revoke tokens for an agent. If scope is None, revoke all.
        Returns the list of tokens that were revoked."""
        ...

    def check(self, token_id: str) -> bool:
        """Check if a token is still valid (not revoked). Returns True if valid."""
        ...

    def get_agent_tokens(self, agent_id: str) -> list[CapabilityToken]:
        """List all tokens (active and revoked) for an agent."""
        ...
