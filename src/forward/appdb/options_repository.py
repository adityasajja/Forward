"""SQL repository for F&O datasets and run records."""

from __future__ import annotations

from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from forward.appdb.engine import utcnow
from forward.appdb.options_schema import (
    options_backtest_runs,
    options_datasets,
    options_forward_runs,
)
from forward.appdb.repository_support import many, one


class OptionsRunRepository:
    """Store imported datasets and their backtest or forward-run records."""

    @staticmethod
    def register_dataset(session: Session, values: dict[str, Any]) -> dict[str, Any]:
        """Insert one validated options dataset index row."""
        row = {**values, "created_at": utcnow()}
        session.execute(insert(options_datasets).values(**row))
        return row

    @staticmethod
    def dataset(session: Session, user_id: str, dataset_id: str) -> dict[str, Any] | None:
        """Read an options dataset only for its owner."""
        statement = select(options_datasets).where(
            options_datasets.c.dataset_id == dataset_id,
            options_datasets.c.user_id == user_id,
        )
        return one(session.execute(statement).first())

    @staticmethod
    def list_datasets(session: Session, user_id: str) -> list[dict[str, Any]]:
        """List one owner's options datasets, newest first."""
        statement = (
            select(options_datasets)
            .where(options_datasets.c.user_id == user_id)
            .order_by(options_datasets.c.created_at.desc())
        )
        return many(session.execute(statement).all())

    @staticmethod
    def create_backtest(session: Session, values: dict[str, Any]) -> dict[str, Any]:
        """Insert one queued F&O backtest row."""
        row = {**values, "created_at": utcnow()}
        session.execute(insert(options_backtest_runs).values(**row))
        return row

    @staticmethod
    def update_backtest(
        session: Session, run_id: str, user_id: str, **values: Any
    ) -> int:
        """Update one backtest row owned by ``user_id``."""
        result = session.execute(
            update(options_backtest_runs)
            .where(
                options_backtest_runs.c.run_id == run_id,
                options_backtest_runs.c.user_id == user_id,
            )
            .values(**values)
        )
        return int(result.rowcount or 0)

    @staticmethod
    def backtest(session: Session, run_id: str, user_id: str) -> dict[str, Any] | None:
        """Read one backtest row only for its owner."""
        statement = select(options_backtest_runs).where(
            options_backtest_runs.c.run_id == run_id,
            options_backtest_runs.c.user_id == user_id,
        )
        return one(session.execute(statement).first())

    @staticmethod
    def create_forward_run(session: Session, values: dict[str, Any]) -> dict[str, Any]:
        """Insert one F&O forward-paper row."""
        row = {**values, "created_at": utcnow()}
        session.execute(insert(options_forward_runs).values(**row))
        return row

    @staticmethod
    def update_forward_run(
        session: Session, run_id: str, user_id: str, **values: Any
    ) -> int:
        """Update one forward-paper row owned by ``user_id``."""
        result = session.execute(
            update(options_forward_runs)
            .where(
                options_forward_runs.c.run_id == run_id,
                options_forward_runs.c.user_id == user_id,
            )
            .values(**values)
        )
        return int(result.rowcount or 0)

    @staticmethod
    def forward_run(session: Session, run_id: str, user_id: str) -> dict[str, Any] | None:
        """Read one forward-paper row only for its owner."""
        statement = select(options_forward_runs).where(
            options_forward_runs.c.run_id == run_id,
            options_forward_runs.c.user_id == user_id,
        )
        return one(session.execute(statement).first())

    @staticmethod
    def list_forward_runs(
        session: Session, user_id: str, limit: int
    ) -> list[dict[str, Any]]:
        """List recent forward-paper rows for one owner."""
        statement = (
            select(options_forward_runs)
            .where(options_forward_runs.c.user_id == user_id)
            .order_by(options_forward_runs.c.created_at.desc())
            .limit(limit)
        )
        return many(session.execute(statement).all())
