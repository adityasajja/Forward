"""SQL adapter for live reconciliation records."""

from __future__ import annotations

from typing import Any

from forward.appdb.audit_repository import AuditRepository
from forward.appdb.engine import AppDatabase
from forward.appdb.order_repository import OrderRepository
from forward.appdb.reconciliation_repository import ReconciliationRepository


class SqlReconciliationStorage:
    """Read platform state and persist reconciliation reports with SQL."""

    def __init__(self, db: AppDatabase) -> None:
        """Create the adapter.

        Args:
            db: The application database connection manager.
        """
        self._db = db

    def open_orders(self, user_id: str) -> list[dict[str, Any]]:
        """Return live open orders for the account owner."""
        with self._db.session() as session:
            rows = OrderRepository.open_orders(session, user_id)
        return [row for row in rows if row["mode"].upper() == "LIVE"]

    def record(self, user_id: str, report: Any, actor: str | None) -> str:
        """Persist one report and its audit event.

        Args:
            user_id: The account owner.
            report: The reconciliation report to persist.
            actor: The user or system actor that started the run.

        Returns:
            The stored run identifier.
        """
        details = [difference.as_dict() for difference in report.differences]
        details.extend(
            {
                "scope": item["scope"],
                "key": item["scope"],
                "kind": "not_comparable",
                "severity": "warning",
                "detail": item["reason"],
            }
            for item in report.incomparable
        )
        with self._db.session() as session:
            row = ReconciliationRepository.record(
                session,
                user_id=user_id,
                scope=report.scope,
                mismatches=details,
                severity=report.severity,
                internal_count=sum(result.platform_count for result in report.results),
                broker_count=sum(result.broker_count for result in report.results),
                error=report.error,
                duration_ms=report.duration_ms,
            )
            run_id = row["run_id"]
            AuditRepository.append(
                session,
                action="reconciliation.run",
                result="failure" if report.severity != "ok" else "success",
                user_id=user_id,
                actor=actor or "system",
                target_type="reconciliation",
                target_id=run_id,
                detail={
                    "scope": report.scope,
                    "severity": report.severity,
                    "mismatches": report.mismatch_count,
                    "incomparable": report.incomparable,
                    "critical": [
                        f"{difference.scope}/{difference.key}"
                        for difference in report.critical
                    ],
                },
            )
        report.run_id = run_id
        return run_id

    def latest(self, user_id: str, scope: str | None) -> dict[str, Any] | None:
        """Return the latest report for an owner and optional scope."""
        with self._db.session() as session:
            return ReconciliationRepository.latest(session, user_id, scope=scope)

    def history(
        self, user_id: str, severity: str | None, limit: int
    ) -> tuple[list[dict[str, Any]], int]:
        """Return a report page and its total count."""
        with self._db.session() as session:
            return ReconciliationRepository.list_for_user(
                session, user_id, severity=severity, limit=limit
            )

    def worst_unacknowledged(self, user_id: str) -> dict[str, Any] | None:
        """Return the worst unacknowledged report for an owner."""
        with self._db.session() as session:
            return ReconciliationRepository.worst_unacknowledged(session, user_id)
