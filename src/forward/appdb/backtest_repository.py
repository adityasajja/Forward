"""SQL repositories for backtest runs and their result artifacts."""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.orm import Session

from forward.appdb.engine import utcnow
from forward.appdb.repository_support import json_text, many, new_id, one
from forward.appdb.strategy_backtest_schema import (
    backtest_curves,
    backtest_monthly,
    backtest_runs,
    backtest_trades,
)

TERMINAL_BACKTEST_STATUSES = frozenset({"COMPLETED", "FAILED", "CANCELLED"})


class BacktestRunRepository:
    """Backtest runs and their lifecycle. Metrics are written once, at completion."""

    @staticmethod
    def create(
        session: Session,
        *,
        user_id: str,
        config: dict[str, Any] | str,
        strategy_id: str | None = None,
        strategy_version: int | None = None,
        engine_key: str | None = None,
    ) -> dict[str, Any]:
        if not engine_key and (strategy_id is None or strategy_version is None):
            raise ValueError(
                "a run needs either engine_key or (strategy_id, strategy_version)"
            )
        now = utcnow()
        row = {
            "run_id": new_id(),
            "user_id": user_id,
            "strategy_id": strategy_id,
            "strategy_version": strategy_version,
            "engine_key": engine_key,
            "created_at": now,
            "started_at": None,
            "finished_at": None,
            "status": "QUEUED",
            "progress": 0.0,
            "config": json_text(config) or "{}",
            "data_fingerprint": None,
            "metrics": None,
            "error": None,
        }
        session.execute(insert(backtest_runs).values(**row))
        return row

    @staticmethod
    def get(session: Session, run_id: str, user_id: str) -> dict[str, Any] | None:
        stmt = select(backtest_runs).where(
            backtest_runs.c.run_id == run_id, backtest_runs.c.user_id == user_id
        )
        return one(session.execute(stmt).first())

    @staticmethod
    def list_for_user(
        session: Session,
        user_id: str,
        *,
        status: str | None = None,
        strategy_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        conditions = [backtest_runs.c.user_id == user_id]
        if status:
            conditions.append(backtest_runs.c.status == status)
        if strategy_id:
            conditions.append(backtest_runs.c.strategy_id == strategy_id)
        total = int(
            session.execute(
                select(func.count()).select_from(backtest_runs).where(*conditions)
            ).scalar()
            or 0
        )
        stmt = (
            select(backtest_runs)
            .where(*conditions)
            .order_by(backtest_runs.c.created_at.desc())
            .limit(max(1, min(limit, 200)))
            .offset(max(0, offset))
        )
        return many(session.execute(stmt).all()), total

    @staticmethod
    def _guard(run_id: str, user_id: str) -> list[Any]:
        """Conditions that only match a run which has not yet finished.

        Every setter uses these, so a finished run cannot be reopened and a
        completed run's metrics cannot be overwritten by a late straggler from
        the worker that produced them.
        """
        return [
            backtest_runs.c.run_id == run_id,
            backtest_runs.c.user_id == user_id,
            backtest_runs.c.status.notin_(sorted(TERMINAL_BACKTEST_STATUSES)),
        ]

    @staticmethod
    def mark_running(session: Session, run_id: str, user_id: str) -> int:
        result = session.execute(
            update(backtest_runs)
            .where(*BacktestRunRepository._guard(run_id, user_id))
            .values(status="RUNNING", started_at=utcnow())
        )
        return int(result.rowcount or 0)

    @staticmethod
    def set_progress(
        session: Session, run_id: str, user_id: str, progress: float
    ) -> int:
        value = max(0.0, min(1.0, float(progress)))
        result = session.execute(
            update(backtest_runs)
            .where(*BacktestRunRepository._guard(run_id, user_id))
            .values(progress=value)
        )
        return int(result.rowcount or 0)

    @staticmethod
    def complete(
        session: Session,
        run_id: str,
        user_id: str,
        *,
        metrics: dict[str, Any],
        data_fingerprint: str | None = None,
    ) -> int:
        result = session.execute(
            update(backtest_runs)
            .where(*BacktestRunRepository._guard(run_id, user_id))
            .values(
                status="COMPLETED",
                progress=1.0,
                finished_at=utcnow(),
                metrics=json_text(metrics),
                data_fingerprint=data_fingerprint,
            )
        )
        return int(result.rowcount or 0)

    @staticmethod
    def fail(session: Session, run_id: str, user_id: str, *, error: str) -> int:
        result = session.execute(
            update(backtest_runs)
            .where(*BacktestRunRepository._guard(run_id, user_id))
            .values(status="FAILED", finished_at=utcnow(), error=str(error)[:2000])
        )
        return int(result.rowcount or 0)

    @staticmethod
    def cancel(session: Session, run_id: str, user_id: str) -> int:
        result = session.execute(
            update(backtest_runs)
            .where(*BacktestRunRepository._guard(run_id, user_id))
            .values(status="CANCELLED", finished_at=utcnow())
        )
        return int(result.rowcount or 0)


class BacktestArtefactRepository:
    """The bulky outputs of a run: trades, curves and the monthly matrix.

    Kept apart from :class:`BacktestRunRepository` because the two have opposite
    access patterns. A run row is read constantly (status polling) and written a
    few times; artefact rows are written once in bulk at completion and read
    only when someone opens the results page. Mixing them would mean the status
    endpoint's query dragging megabytes of curve data around.

    Every write here is **replace-on-complete**, not append: a run's artefacts
    are a function of the run, so re-writing them is idempotent. Appending would
    let a retried completion double a run's trade list, and a doubled trade list
    silently doubles the P&L totals computed from it.
    """

    @staticmethod
    def save_trades(
        session: Session, run_id: str, trades: list[dict[str, Any]]
    ) -> int:
        session.execute(delete(backtest_trades).where(backtest_trades.c.run_id == run_id))
        if not trades:
            return 0
        rows = []
        for seq, trade in enumerate(trades):
            rows.append({**trade, "run_id": run_id, "seq": seq})
        session.execute(insert(backtest_trades), rows)
        return len(rows)

    @staticmethod
    def trades(
        session: Session,
        run_id: str,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        stmt = (
            select(backtest_trades)
            .where(backtest_trades.c.run_id == run_id)
            .order_by(backtest_trades.c.seq)
            .offset(max(0, offset))
        )
        if limit is not None:
            stmt = stmt.limit(max(1, limit))
        return many(session.execute(stmt).all())

    @staticmethod
    def trade_count(session: Session, run_id: str) -> int:
        stmt = select(func.count()).select_from(backtest_trades).where(
            backtest_trades.c.run_id == run_id
        )
        return int(session.execute(stmt).scalar() or 0)

    @staticmethod
    def trade(session: Session, run_id: str, seq: int) -> dict[str, Any] | None:
        stmt = select(backtest_trades).where(
            backtest_trades.c.run_id == run_id, backtest_trades.c.seq == int(seq)
        )
        return one(session.execute(stmt).first())

    @staticmethod
    def save_curve(
        session: Session, run_id: str, kind: str, points: list[tuple[Any, float]]
    ) -> int:
        session.execute(
            delete(backtest_curves).where(
                backtest_curves.c.run_id == run_id, backtest_curves.c.kind == kind
            )
        )
        if not points:
            return 0
        rows = [
            {"run_id": run_id, "kind": kind, "seq": i, "ts": ts, "value": float(value)}
            for i, (ts, value) in enumerate(points)
        ]
        session.execute(insert(backtest_curves), rows)
        return len(rows)

    @staticmethod
    def curve(
        session: Session, run_id: str, kind: str, *, limit: int | None = None
    ) -> list[dict[str, Any]]:
        stmt = (
            select(backtest_curves)
            .where(backtest_curves.c.run_id == run_id, backtest_curves.c.kind == kind)
            .order_by(backtest_curves.c.seq)
        )
        if limit is not None:
            stmt = stmt.limit(max(1, limit))
        return many(session.execute(stmt).all())

    @staticmethod
    def save_monthly(session: Session, run_id: str, rows_in: list[dict[str, Any]]) -> int:
        session.execute(delete(backtest_monthly).where(backtest_monthly.c.run_id == run_id))
        if not rows_in:
            return 0
        rows = [{**row, "run_id": run_id} for row in rows_in]
        session.execute(insert(backtest_monthly), rows)
        return len(rows)

    @staticmethod
    def monthly(session: Session, run_id: str) -> list[dict[str, Any]]:
        stmt = (
            select(backtest_monthly)
            .where(backtest_monthly.c.run_id == run_id)
            .order_by(backtest_monthly.c.year, backtest_monthly.c.month)
        )
        return many(session.execute(stmt).all())

    @staticmethod
    def delete_for_run(session: Session, run_id: str) -> None:
        """Used when a run fails after partial writes, so no half a result survives."""
        for table in (backtest_trades, backtest_curves, backtest_monthly):
            session.execute(delete(table).where(table.c.run_id == run_id))
