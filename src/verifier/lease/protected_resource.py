"""Protected Resource — the fencing-token enforcement point.

The protected resource is the downstream entity that agents write to.
It MUST check the fencing token on every write and reject any write whose
token is lower than the highest one it has already accepted for that
resource_id — Kleppmann, "How to do distributed locking," 2016.

A stale agent's write is structurally REJECTED, not merely logged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol


# ---------------------------------------------------------------------------
# WriteResult — outcome of a protected write attempt
# ---------------------------------------------------------------------------

class WriteStatus(Enum):
    """Outcome of a write attempt to a protected resource."""
    ACCEPTED = "accepted"
    REJECTED_STALE_TOKEN = "rejected_stale_token"


@dataclass
class WriteResult:
    """Result of a write attempt to a ProtectedResource.

    If rejected, includes the reason and the conflicting token values
    for diagnostic and fencing_conflict event generation.
    """

    status: WriteStatus
    resource_id: str
    fencing_token: int
    payload: dict[str, Any]
    highest_accepted_token: int | None = None  # set on rejection for diagnostics

    @property
    def accepted(self) -> bool:
        return self.status == WriteStatus.ACCEPTED

    def to_dict(self) -> dict[str, Any]:
        """Serialize for WebSocket transport."""
        return {
            "status": self.status.value,
            "resource_id": self.resource_id,
            "fencing_token": self.fencing_token,
            "payload": self.payload,
            "highest_accepted_token": self.highest_accepted_token,
        }


# ---------------------------------------------------------------------------
# ProtectedResource protocol — Section 6.5
# ---------------------------------------------------------------------------

class ProtectedResourceProtocol(Protocol):
    """Protocol for a fencing-token-protected resource (Section 6.5).

    MUST reject — not merely log — any write whose fencing_token is
    lower than the highest token this resource has already accepted
    for that resource_id.
    """

    def write(self, resource_id: str, fencing_token: int, payload: dict[str, Any]) -> WriteResult:
        """Attempt a write, enforcing fencing-token monotonicity."""
        ...

    def highest_accepted(self, resource_id: str) -> int:
        """Return the highest fencing token accepted for this resource.
        Returns 0 if no writes have been accepted yet.

        Used by the guard registry (EngineContext) for fencing_token_valid checks.
        """
        ...

    def get_writes(self, resource_id: str) -> list[dict[str, Any]]:
        """Return the audit log of accepted writes for a resource."""
        ...
