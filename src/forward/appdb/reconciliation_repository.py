"""SQL repository for reconciliation run records."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session

from forward.appdb.engine import utcnow
from forward.appdb.execution_schema import reconciliation_runs
from forward.appdb.repository_support import json_text, many, new_id, one


class ReconciliationRepository:
    """Persisted reconciliation runs. A mismatch is a row, not a log line."""

    @staticmethod
    def record(
        session: Session,
        *,
        user_id: str,
        scope: str,
        mismatches: list[dict[str, Any]] | None = None,
        severity: str = "ok",
        internal_count: int | None = None,
        broker_count: int | None = None,
        error: str | None = None,
        duration_ms: int | None = None,
        ts: datetime | None = None,
    ) -> dict[str, Any]:
        """Persist one run, refusing to call a run with differences ``ok``.

        The invariant is enforced here because it is the whole point of the
        table: a reconciliation that found mismatches and recorded ``ok`` is
        indistinguishable from one that found nothing, and the silent version is
        the failure mode the brief forbids.
        """
        details = list(mismatches or [])
        severity = (severity or "ok").strip().lower()
        if severity not in ("ok", "warning", "critical"):
            raise ValueError(f"severity must be ok/warning/critical, got {severity!r}")
        if details and severity == "ok":
            raise ValueError(
                f"{len(details)} mismatch(es) recorded with severity 'ok' — "
                "a run that found differences must not report itself clean"
            )
        if error and severity == "ok":
            raise ValueError("a run that errored must not report itself clean")
        row = {
            "run_id": new_id(),
            "user_id": user_id,
            "ts": ts or utcnow(),
            "scope": scope,
            "internal_count": internal_count,
            "broker_count": broker_count,
            "mismatches": len(details),
            "severity": severity,
            "detail": json_text(details) if details else None,
            "error": (error or None) and str(error)[:255],
            "duration_ms": duration_ms,
        }
        session.execute(insert(reconciliation_runs).values(**row))
        return row

    @staticmethod
    def latest(
        session: Session, user_id: str, *, scope: str | None = None
    ) -> dict[str, Any] | None:
        stmt = select(reconciliation_runs).where(
            reconciliation_runs.c.user_id == user_id
        )
        if scope:
            stmt = stmt.where(reconciliation_runs.c.scope == scope)
        stmt = stmt.order_by(reconciliation_runs.c.ts.desc()).limit(1)
        return one(session.execute(stmt).first())

    @staticmethod
    def list_for_user(
        session: Session,
        user_id: str,
        *,
        severity: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        conditions = [reconciliation_runs.c.user_id == user_id]
        if severity:
            conditions.append(reconciliation_runs.c.severity == severity)
        total = int(
            session.execute(
                select(func.count()).select_from(reconciliation_runs).where(*conditions)
            ).scalar()
            or 0
        )
        stmt = (
            select(reconciliation_runs)
            .where(*conditions)
            .order_by(reconciliation_runs.c.ts.desc())
            .limit(max(1, min(limit, 500)))
            .offset(max(0, offset))
        )
        return many(session.execute(stmt).all()), total

    @staticmethod
    def worst_unacknowledged(session: Session, user_id: str) -> dict[str, Any] | None:
        """The most recent run that was not clean, if it is still the most recent.

        Returns ``None`` once a clean run supersedes it, so the dashboard banner
        clears when the problem is fixed without anyone having to dismiss it.
        """
        latest = ReconciliationRepository.latest(session, user_id)
        if latest is None or latest["severity"] == "ok":
            return None
        return latest
