"""ProtectedResource implementation — fencing-token enforcement point.

This is where the rubber meets the road for hard invariant prevention:
a stale agent's write is structurally REJECTED, not merely logged.

The resource tracks the highest accepted fencing token per resource_id
and rejects any write with a lower token — Kleppmann, "How to do
distributed locking," 2016.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from backend.verifier.lease.protected_resource import WriteResult, WriteStatus

logger = logging.getLogger(__name__)


class ProtectedResourceImpl:
    """Concrete implementation of the fencing-token-protected resource.

    MUST reject — not merely log — any write whose fencing_token is
    lower than the highest token this resource has already accepted
    for that resource_id.
    """

    def __init__(self):
        self._highest_token: dict[str, int] = {}   # resource_id -> highest accepted token
        self._write_log: dict[str, list[dict[str, Any]]] = {}  # resource_id -> writes

    def write(
        self,
        resource_id: str,
        fencing_token: int,
        payload: dict[str, Any],
    ) -> WriteResult:
        """Attempt a write, enforcing fencing-token monotonicity.

        # fencing-token check — Kleppmann, "How to do distributed locking," 2016
        Rejects any write whose token is lower than the highest already
        accepted. This is structural prevention: the stale agent's write
        is rejected outright, not logged after the fact.

        Args:
            resource_id: The resource being written to.
            fencing_token: The fencing token accompanying the write.
            payload: The data to write.

        Returns:
            WriteResult indicating accepted or rejected (with diagnostics).
        """
        current_highest = self._highest_token.get(resource_id, 0)

        # Fencing check: reject if token is stale
        if fencing_token < current_highest:
            logger.warning(
                "FENCING REJECTION: resource=%s token=%d < highest=%d — "
                "stale write structurally rejected (not just logged)",
                resource_id, fencing_token, current_highest,
            )
            return WriteResult(
                status=WriteStatus.REJECTED_STALE_TOKEN,
                resource_id=resource_id,
                fencing_token=fencing_token,
                payload=payload,
                highest_accepted_token=current_highest,
            )

        # Accept the write and update the highest token
        self._highest_token[resource_id] = fencing_token

        # Record in audit log
        write_record = {
            "fencing_token": fencing_token,
            "payload": payload,
        }
        if resource_id not in self._write_log:
            self._write_log[resource_id] = []
        self._write_log[resource_id].append(write_record)

        logger.info(
            "Write accepted: resource=%s token=%d payload=%s",
            resource_id, fencing_token, payload,
        )
        return WriteResult(
            status=WriteStatus.ACCEPTED,
            resource_id=resource_id,
            fencing_token=fencing_token,
            payload=payload,
        )

    def highest_accepted(self, resource_id: str) -> int:
        """Return the highest fencing token accepted for this resource.

        Returns 0 if no writes have been accepted yet.
        Used by the guard registry (EngineContext) for fencing_token_valid checks.
        """
        return self._highest_token.get(resource_id, 0)

    def get_writes(self, resource_id: str) -> list[dict[str, Any]]:
        """Return the audit log of accepted writes for a resource."""
        return list(self._write_log.get(resource_id, []))

    def get_all_resources(self) -> dict[str, int]:
        """Return all resources and their highest accepted tokens."""
        return dict(self._highest_token)

    def reset(self) -> None:
        """Clear all state for re-runnability (Patch §8)."""
        self._highest_token.clear()
        self._write_log.clear()
