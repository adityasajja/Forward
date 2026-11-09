"""Write and read the local audit trail."""

from __future__ import annotations

from typing import Any

from forward.audit.events import build_audit_event
from forward.core.audit_ports import (
    AuditLogPort,
)


class AuditLogService:
    """Record file audit events and read their recent history."""

    def __init__(self, storage: AuditLogPort) -> None:
        """Create the audit log service.

        Args:
            storage: Port that appends and reads JSONL audit records.
        """
        self._storage = storage

    def append(
        self,
        *,
        actor: str,
        action: str,
        subject: str,
        detail: str | None = None,
    ) -> dict[str, Any]:
        """Create and append one immutable audit event.

        Args:
            actor: User who performed the action.
            action: Stable name for the action.
            subject: Object changed by the action.
            detail: Optional reason or outcome description.

        Returns:
            The event sent to the storage adapter.
        """
        entry = build_audit_event(
            actor=actor,
            action=action,
            subject=subject,
            detail=detail,
        )
        self._storage.append(entry)
        return entry

    def record(
        self,
        *,
        actor: str,
        action: str,
        subject: str,
        detail: Any,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Write an audit event with the platform's full event fields.

        Args:
            actor: User who performed the action.
            action: Stable name for the action.
            subject: Object changed by the action.
            detail: Event details, encoded as JSON when not already text.
            user_id: Optional user identifier for audit queries.

        Returns:
            The event written to the configured audit storage.
        """
        entry = build_audit_event(
            actor=actor,
            action=action,
            subject=subject,
            detail=detail,
            user_id=user_id,
        )
        self._storage.append(entry)
        return entry

    def recent(self, limit: int = 200) -> list[dict[str, Any]]:
        """Return recent audit events, newest first.

        Args:
            limit: Maximum number of events to return.

        Returns:
            Recent audit records in newest-first order.
        """
        return self._storage.recent(limit)

    def path(self) -> str:
        """Return the configured JSONL audit path."""
        return self._storage.path()
