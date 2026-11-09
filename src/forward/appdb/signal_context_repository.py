"""SQL repository for persisted signal contexts."""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, func, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from forward.appdb.repository_support import decode_json_fields
from forward.appdb.trade_schema import signal_contexts


class SignalContextRepository:
    """Read and write enriched signal contexts."""

    @staticmethod
    def save(session: Session, row: dict[str, Any]) -> int:
        """Insert one context row, or return zero when its key already exists.

        Args:
            session: The caller's database transaction.
            row: Context fields to store.

        Returns:
            One when inserted, or zero when the same user and signal exist.

        Raises:
            IntegrityError: If the insert fails for a reason other than a duplicate
                user and signal key.
        """
        statement = insert(signal_contexts).values(**row)
        try:
            with session.begin_nested():
                session.execute(statement)
        except IntegrityError:
            existing = SignalContextRepository.get(session, row["user_id"], row["signal_id"])
            if existing is None:
                raise
            return 0
        return 1

    @staticmethod
    def replace_for_run(session: Session, run_id: str, rows: list[dict[str, Any]]) -> int:
        """Replace every context row stored for a backtest run.

        Args:
            session: The caller's database transaction.
            run_id: Backtest run whose rows should be replaced.
            rows: Complete set of context rows for the run.

        Returns:
            Number of stored rows.
        """
        session.execute(delete(signal_contexts).where(signal_contexts.c.run_id == run_id))
        if not rows:
            return 0
        session.execute(insert(signal_contexts), rows)
        return len(rows)

    @staticmethod
    def get(session: Session, user_id: str, signal_id: str) -> dict[str, Any] | None:
        """Return one context owned by a user.

        Args:
            session: The caller's database transaction.
            user_id: Account that owns the context.
            signal_id: Stable signal identifier.

        Returns:
            The decoded row, or ``None`` when it does not exist.
        """
        statement = select(signal_contexts).where(
            signal_contexts.c.user_id == user_id,
            signal_contexts.c.signal_id == signal_id,
        )
        row = session.execute(statement).mappings().first()
        if row is None:
            return None
        return decode_json_fields(row, _SIGNAL_CONTEXT_JSON_FIELDS)

    @staticmethod
    def list(
        session: Session,
        user_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        source: str | None = None,
        strategy_id: str | None = None,
        klass: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return a page of contexts owned by a user.

        Args:
            session: The caller's database transaction.
            user_id: Account that owns the contexts.
            limit: Maximum number of rows to return.
            offset: Number of matching rows to skip.
            source: Optional source filter.
            strategy_id: Optional strategy filter.
            klass: Optional context-class filter.

        Returns:
            Decoded contexts, newest first.
        """
        statement = select(signal_contexts).where(signal_contexts.c.user_id == user_id)
        if source:
            statement = statement.where(signal_contexts.c.signal_source == source)
        if strategy_id:
            statement = statement.where(signal_contexts.c.strategy_id == strategy_id)
        if klass:
            statement = statement.where(signal_contexts.c.context_class == klass)
        statement = statement.order_by(
            signal_contexts.c.signal_ts.desc(), signal_contexts.c.signal_id
        )
        statement = statement.limit(max(1, limit)).offset(max(0, offset))
        rows = session.execute(statement).mappings()
        return [decode_json_fields(row, _SIGNAL_CONTEXT_JSON_FIELDS) for row in rows]

    @staticmethod
    def count(
        session: Session,
        user_id: str,
        *,
        source: str | None = None,
        strategy_id: str | None = None,
        klass: str | None = None,
    ) -> int:
        """Count contexts owned by a user that match the supplied filters.

        Args:
            session: The caller's database transaction.
            user_id: Account that owns the contexts.
            source: Optional source filter.
            strategy_id: Optional strategy filter.
            klass: Optional context-class filter.

        Returns:
            Number of matching contexts.
        """
        statement = (
            select(func.count())
            .select_from(signal_contexts)
            .where(signal_contexts.c.user_id == user_id)
        )
        if source:
            statement = statement.where(signal_contexts.c.signal_source == source)
        if strategy_id:
            statement = statement.where(signal_contexts.c.strategy_id == strategy_id)
        if klass:
            statement = statement.where(signal_contexts.c.context_class == klass)
        return int(session.execute(statement).scalar() or 0)

    @staticmethod
    def for_run(session: Session, run_id: str, user_id: str) -> list[dict[str, Any]]:
        """Return one user's contexts for a backtest run.

        Args:
            session: The caller's database transaction.
            run_id: Backtest run identifier.
            user_id: Account that owns the rows.

        Returns:
            Decoded rows ordered by trade ID.
        """
        statement = (
            select(signal_contexts)
            .where(
                signal_contexts.c.run_id == run_id,
                signal_contexts.c.user_id == user_id,
            )
            .order_by(signal_contexts.c.trade_id)
        )
        rows = session.execute(statement).mappings()
        return [decode_json_fields(row, _SIGNAL_CONTEXT_JSON_FIELDS) for row in rows]

    @staticmethod
    def analytics_rows(
        session: Session,
        user_id: str,
        *,
        strategy_id: str | None = None,
        source: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return all of a user's contexts for outcome analysis.

        There is no strategy-version filter. A context is shared across strategy
        versions, and the service scopes each outcome to its own version.

        Args:
            session: The caller's database transaction.
            user_id: Account that owns the contexts.
            strategy_id: Optional strategy filter.
            source: Optional source filter.

        Returns:
            Decoded contexts in signal-time order.
        """
        statement = select(signal_contexts).where(signal_contexts.c.user_id == user_id)
        if strategy_id:
            statement = statement.where(signal_contexts.c.strategy_id == strategy_id)
        if source:
            statement = statement.where(signal_contexts.c.signal_source == source)
        statement = statement.order_by(signal_contexts.c.signal_ts)
        rows = session.execute(statement).mappings()
        return [decode_json_fields(row, _SIGNAL_CONTEXT_JSON_FIELDS) for row in rows]


_SIGNAL_CONTEXT_JSON_FIELDS = (
    "market_context",
    "sector_context",
    "stock_context",
    "score_breakdown",
    "missing_fields",
    "benchmark_provenance",
)
