"""SQL adapter for audit event queries."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from forward.appdb.audit_repository import AuditRepository
from forward.appdb.engine import AppDatabase
from forward.audit.events import build_audit_event
from forward.core.audit_ports import AuditLogPort


def record_audit_event(
    session: Session, audit_log: AuditLogPort, **event: Any
) -> str:
    """Write one audit event to the file and database sinks.

    Args:
        session: Open database session for the caller's transaction.
        audit_log: Adapter for the append-only file sink.
        **event: Fields accepted by ``AuditRepository.append``.

    Returns:
        Database event identifier.
    """
    audit_log.append(build_audit_event(**event))
    return AuditRepository.append(session, **event)


class SqlAuditStorage:
    """Run audit repository queries inside managed database sessions."""

    def __init__(self, db: AppDatabase) -> None:
        """Create the SQL audit adapter.

        Args:
            db: The application database connection manager.
        """
        self._db = db

    def query(self, **filters: Any) -> tuple[list[dict[str, Any]], int]:
        """Return a filtered event page from the database.

        Args:
            **filters: Audit filters and page bounds.

        Returns:
            The event rows and total number of matches.
        """
        with self._db.session() as session:
            return AuditRepository.query(session, **filters)

    def get(self, event_id: str) -> dict[str, Any] | None:
        """Read one event from the database.

        Args:
            event_id: The event identifier.

        Returns:
            The event, or ``None`` when it does not exist.
        """
        with self._db.session() as session:
            return AuditRepository.get(session, event_id)

    def actions(self) -> list[str]:
        """Read distinct action names from the database."""
        with self._db.session() as session:
            return AuditRepository.distinct_actions(session)
