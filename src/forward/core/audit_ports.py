"""Ports for audit storage and file logging."""

from __future__ import annotations

from typing import Any, Protocol


class AuditStoragePort(Protocol):
    """Read audit events stored in the application database."""

    def query(self, **filters: Any) -> tuple[list[dict[str, Any]], int]:
        """Return an audit event page and its total row count.

        Args:
            **filters: Event filters and page bounds supported by the audit store.

        Returns:
            The event rows and the total matching row count.
        """

    def get(self, event_id: str) -> dict[str, Any] | None:
        """Return one audit event by its ID.

        Args:
            event_id: The event identifier.

        Returns:
            The event, or ``None`` when it does not exist.
        """

    def actions(self) -> list[str]:
        """Return distinct recorded action names."""


class AuditLogPort(Protocol):
    """Append and read the local JSONL audit trail."""

    def append(self, record: dict[str, Any]) -> None:
        """Append one audit record without changing earlier records.

        Args:
            record: Audit fields to store as one JSON line.
        """

    def recent(self, limit: int) -> list[dict[str, Any]]:
        """Return the newest stored records first.

        Args:
            limit: Maximum number of records to return.

        Returns:
            Recent audit records in newest-first order.
        """

    def path(self) -> str:
        """Return the configured audit file path for display."""
