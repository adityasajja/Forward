"""Audit query service.

The route layer may not touch SQL, so reading the audit trail goes through here.
That is not ceremony: it is what keeps "who is allowed to read the audit trail"
in one place, next to the query, rather than in a route that has to remember to
check it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from forward.core.audit_ports import (
    AuditStoragePort,
)


class AuditService:
    def __init__(self, storage: AuditStoragePort) -> None:
        """Create the audit query service.

        Args:
            storage: The output port used to read audit records.
        """
        self._storage = storage

    def query(
        self,
        *,
        user_id: str | None = None,
        action: str | None = None,
        result: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return matching audit rows and the total result count.

        Args:
            user_id: Optional actor user identifier.
            action: Optional action name.
            result: Optional event result.
            since: Inclusive start timestamp.
            until: Inclusive end timestamp.
            limit: Maximum number of rows to return.
            offset: Number of matching rows to skip.

        Returns:
            The requested event page and total matching row count.
        """
        return self._storage.query(
            user_id=user_id,
            action=action,
            result=result,
            # The API accepts tz-aware datetimes; storage is naive-UTC, so
            # normalise here rather than letting a comparison silently fail.
            since=since.replace(tzinfo=None) if since and since.tzinfo else since,
            until=until.replace(tzinfo=None) if until and until.tzinfo else until,
            limit=limit,
            offset=offset,
        )

    def get(self, event_id: str) -> dict[str, Any] | None:
        """Return one event by ID.

        Args:
            event_id: The audit event identifier.

        Returns:
            The event row, or ``None`` when it does not exist.
        """
        return self._storage.get(event_id)

    def actions(self) -> list[str]:
        """Return the distinct action names recorded in the audit log.

        Returns:
            Sorted action names.
        """
        return self._storage.actions()


__all__ = ["AuditService"]
