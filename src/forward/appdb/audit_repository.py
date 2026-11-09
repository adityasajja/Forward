"""SQL repository for audit events."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session

from forward.appdb.audit_schema import audit_events
from forward.appdb.engine import utcnow
from forward.appdb.repository_support import many, new_id, one


class AuditRepository:
    """Append audit events and read them for account reports."""

    @staticmethod
    def append(
        session: Session,
        *,
        action: str,
        result: str = "success",
        user_id: str | None = None,
        actor: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        strategy_id: str | None = None,
        strategy_version: int | None = None,
        request_id: str | None = None,
        detail: dict[str, Any] | str | None = None,
        ip: str | None = None,
        ts: datetime | None = None,
    ) -> str:
        """Append one audit event in the caller's transaction.

        Args:
            session: The caller's database transaction.
            action: Stable name for the event.
            result: Event outcome.
            user_id: Account linked to the event, when available.
            actor: Person or service that caused the event.
            target_type: Kind of record affected by the event.
            target_id: ID of the affected record.
            strategy_id: Strategy linked to the event, when available.
            strategy_version: Exact strategy version, when available.
            request_id: Request that caused the event, when available.
            detail: Event details as a mapping or serialized text.
            ip: Request address, when available.
            ts: Event time, or current UTC when not supplied.

        Returns:
            ID of the stored audit event.
        """
        event_id = new_id()
        detail_text = json.dumps(detail, default=str) if isinstance(detail, dict) else detail
        session.execute(
            insert(audit_events).values(
                event_id=event_id,
                ts=ts or utcnow(),
                user_id=user_id,
                actor=(actor or "")[:64] or None,
                action=action[:64],
                target_type=target_type,
                target_id=target_id,
                strategy_id=strategy_id,
                strategy_version=strategy_version,
                request_id=request_id,
                result=result[:16],
                detail=detail_text,
                ip=(ip or "")[:64] or None,
            )
        )
        return event_id

    @staticmethod
    def query(
        session: Session,
        *,
        user_id: str | None = None,
        action: str | None = None,
        result: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return a filtered page of audit events and its total count.

        Args:
            session: The caller's database transaction.
            user_id: Optional account filter.
            action: Optional event-name filter.
            result: Optional outcome filter.
            since: Optional inclusive start time.
            until: Optional inclusive end time.
            limit: Maximum rows to return.
            offset: Number of matching rows to skip.

        Returns:
            Newest matching events and the total count before pagination.
        """
        conditions = []
        if user_id:
            conditions.append(audit_events.c.user_id == user_id)
        if action:
            conditions.append(audit_events.c.action == action)
        if result:
            conditions.append(audit_events.c.result == result)
        if since:
            conditions.append(audit_events.c.ts >= since)
        if until:
            conditions.append(audit_events.c.ts <= until)

        count_statement = select(func.count()).select_from(audit_events).where(*conditions)
        total = int(session.execute(count_statement).scalar() or 0)
        statement = (
            select(audit_events)
            .where(*conditions)
            .order_by(audit_events.c.ts.desc())
            .limit(max(1, min(limit, 1000)))
            .offset(max(0, offset))
        )
        return many(session.execute(statement).all()), total

    @staticmethod
    def get(session: Session, event_id: str) -> dict[str, Any] | None:
        """Return one event by its ID.

        Args:
            session: The caller's database transaction.
            event_id: Audit event identifier.

        Returns:
            The event row, or ``None`` when it does not exist.
        """
        statement = select(audit_events).where(audit_events.c.event_id == event_id)
        return one(session.execute(statement).first())

    @staticmethod
    def distinct_actions(session: Session) -> list[str]:
        """Return all stored event names in alphabetical order.

        Args:
            session: The caller's database transaction.

        Returns:
            Unique event names.
        """
        statement = select(audit_events.c.action).distinct().order_by(audit_events.c.action)
        return [str(row[0]) for row in session.execute(statement).all()]
