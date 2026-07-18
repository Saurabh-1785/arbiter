"""Capability store implementation — token issuance, revocation, and validation.

Every agent receives short-lived, narrowly-scoped, revocable capability
tokens. The moment the verifier confirms a violation, it revokes the
offending agent's tokens. The agent's next tool call is rejected at the
boundary — the action is gated, not the process.
"""

from __future__ import annotations

import uuid
import logging
from typing import Any

from src.verifier.events import HLCTimestamp
from src.verifier.capability.tokens import CapabilityToken

logger = logging.getLogger(__name__)


class CapabilityStoreImpl:
    """Concrete implementation of the capability token store.

    Manages token lifecycle: issue -> check -> revoke.
    Revocation can target a specific scope or all scopes for an agent.
    """

    def __init__(self):
        self._tokens: dict[str, CapabilityToken] = {}       # token_id -> token
        self._agent_tokens: dict[str, list[str]] = {}         # agent_id -> [token_ids]
        self._revocation_log: list[dict[str, Any]] = []       # audit trail

    def issue(
        self,
        agent_id: str,
        scope: str,
        at: HLCTimestamp,
    ) -> CapabilityToken:
        """Issue a new capability token for an agent+scope pair.

        Args:
            agent_id: The agent receiving the token.
            scope: The scope of the token (e.g., "tool:payment.charge", "tool:*").
            at: HLC timestamp of issuance.

        Returns:
            A new CapabilityToken.
        """
        token = CapabilityToken(
            token_id=str(uuid.uuid4()),
            agent_id=agent_id,
            scope=scope,
            issued_at=at,
            revoked=False,
        )

        self._tokens[token.token_id] = token
        if agent_id not in self._agent_tokens:
            self._agent_tokens[agent_id] = []
        self._agent_tokens[agent_id].append(token.token_id)

        logger.info("Token issued: %s for %s scope=%s", token.token_id[:8], agent_id, scope)
        return token

    def revoke(
        self,
        agent_id: str,
        scope: str | None = None,
    ) -> list[CapabilityToken]:
        """Revoke tokens for an agent. If scope is None, revoke all.

        Args:
            agent_id: The agent whose tokens to revoke.
            scope: Optional specific scope to revoke. None = revoke all.

        Returns:
            List of tokens that were revoked.
        """
        revoked = []
        token_ids = self._agent_tokens.get(agent_id, [])

        for tid in token_ids:
            token = self._tokens.get(tid)
            if token is None or token.revoked:
                continue

            if scope is None or self._scope_matches(token.scope, scope):
                token.revoked = True
                revoked.append(token)

                self._revocation_log.append({
                    "token_id": token.token_id,
                    "agent_id": agent_id,
                    "scope": token.scope,
                    "reason": f"Revoked (scope_filter={scope})",
                })

                logger.warning(
                    "TOKEN REVOKED: %s for %s scope=%s",
                    token.token_id[:8], agent_id, token.scope,
                )

        return revoked

    def check(self, token_id: str) -> bool:
        """Check if a token is still valid (not revoked).

        Args:
            token_id: The token to check.

        Returns:
            True if the token exists and is not revoked.
        """
        token = self._tokens.get(token_id)
        if token is None:
            return False
        return not token.revoked

    def get_agent_tokens(self, agent_id: str) -> list[CapabilityToken]:
        """List all tokens (active and revoked) for an agent."""
        token_ids = self._agent_tokens.get(agent_id, [])
        return [self._tokens[tid] for tid in token_ids if tid in self._tokens]

    def get_active_tokens(self, agent_id: str) -> list[CapabilityToken]:
        """List only active (non-revoked) tokens for an agent."""
        return [t for t in self.get_agent_tokens(agent_id) if not t.revoked]

    def get_revocation_log(self) -> list[dict[str, Any]]:
        """Return the full revocation audit log."""
        return list(self._revocation_log)

    def _scope_matches(self, token_scope: str, filter_scope: str) -> bool:
        """Check if a token's scope matches a filter scope.

        Supports wildcard matching: "tool:*" matches "tool:payment.charge".
        """
        if token_scope == filter_scope:
            return True
        if filter_scope.endswith("*"):
            prefix = filter_scope[:-1]
            return token_scope.startswith(prefix)
        if token_scope.endswith("*"):
            prefix = token_scope[:-1]
            return filter_scope.startswith(prefix)
        return False

    def reset(self) -> None:
        """Clear all state for re-runnability (Patch §8)."""
        self._tokens.clear()
        self._agent_tokens.clear()
        self._revocation_log.clear()
