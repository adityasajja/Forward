"""SQL storage adapter for signal-context use cases."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import select

from forward.appdb.backtest_repository import (
    BacktestArtefactRepository,
    BacktestRunRepository,
)
from forward.appdb.engine import AppDatabase
from forward.appdb.execution_schema import orders
from forward.appdb.learning_observation_repository import LearningObservationRepository
from forward.appdb.signal_context_repository import SignalContextRepository
from forward.appdb.strategy_backtest_schema import backtest_runs, backtest_trades
from forward.appdb.trade_journal_repository import TradeJournalRepository
from forward.core.signal_context_ports import (
    SignalContextStoragePort,
)


class SqlSignalContextStorage(SignalContextStoragePort):
    """Run signal-context storage operations in database sessions.

    Args:
        database: Database that owns the SQL session lifecycle.
    """

    def __init__(self, database: AppDatabase) -> None:
        self._database = database

    def save_context(self, row: dict[str, Any]) -> int:
        """Save one context row.

        Args:
            row: Context fields accepted by the SQL repository.

        Returns:
            One when the row was inserted, or zero on a duplicate signal.
        """
        with self._database.session() as session:
            return SignalContextRepository.save(session, row)

    def replace_run_contexts(self, run_id: str, rows: list[dict[str, Any]]) -> int:
        """Replace a run's context rows in one transaction.

        Args:
            run_id: Backtest run whose rows should be replaced.
            rows: Complete set of context rows for the run.

        Returns:
            Number of stored context rows.
        """
        with self._database.session() as session:
            return SignalContextRepository.replace_for_run(session, run_id, rows)

    def list_contexts(
        self,
        user_id: str,
        *,
        limit: int,
        offset: int,
        source: str | None,
        strategy_id: str | None,
        context_class: str | None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return one user's filtered context page and total count.

        Args:
            user_id: Account that owns the contexts.
            limit: Maximum rows in the page.
            offset: Number of matching rows to skip.
            source: Optional signal source filter.
            strategy_id: Optional strategy filter.
            context_class: Optional context class filter.

        Returns:
            The page rows and the count before pagination.
        """
        with self._database.session() as session:
            rows = SignalContextRepository.list(
                session,
                user_id,
                limit=limit,
                offset=offset,
                source=source,
                strategy_id=strategy_id,
                klass=context_class,
            )
            total = SignalContextRepository.count(
                session,
                user_id,
                source=source,
                strategy_id=strategy_id,
                klass=context_class,
            )
        return rows, total

    def context(self, user_id: str, signal_id: str) -> dict[str, Any] | None:
        """Return one context owned by an account.

        Args:
            user_id: Account that owns the signal.
            signal_id: Stable signal identifier.

        Returns:
            The context row, or ``None`` when the user has no matching row.
        """
        with self._database.session() as session:
            return SignalContextRepository.get(session, user_id, signal_id)

    def analytics_contexts(
        self,
        user_id: str,
        *,
        strategy_id: str | None,
        source: str | None,
    ) -> list[dict[str, Any]]:
        """Return stored contexts for outcome analysis.

        Args:
            user_id: Account that owns the contexts.
            strategy_id: Optional strategy filter.
            source: Optional signal source filter.

        Returns:
            Context rows in signal-time order.
        """
        with self._database.session() as session:
            return SignalContextRepository.analytics_rows(
                session, user_id, strategy_id=strategy_id, source=source
            )

    def outcome_data(
        self,
        user_id: str,
        trade_ids: Sequence[int],
        signal_ids: Sequence[str],
        strategy_version: int | None,
    ) -> dict[str, Any]:
        """Read the stored records used to resolve context outcomes.

        Args:
            user_id: Account that owns the contexts and live outcomes.
            trade_ids: Backtest trade identifiers referenced by the contexts.
            signal_ids: Signal identifiers used to find matching orders.
            strategy_version: Optional strategy version that limits live orders.

        Returns:
            Backtest trades keyed by trade ID, orders keyed by signal ID, and
            the user's closed journal episodes.
        """
        trades: dict[int, dict[str, Any]] = {}
        matching_orders: dict[str, dict[str, Any]] = {}
        episodes: list[dict[str, Any]] = []
        with self._database.session() as session:
            if trade_ids:
                statement = (
                    select(backtest_trades)
                    .join(
                        backtest_runs,
                        backtest_runs.c.run_id == backtest_trades.c.run_id,
                    )
                    .where(
                        backtest_runs.c.user_id == user_id,
                        backtest_trades.c.trade_id.in_(set(trade_ids)),
                    )
                )
                trades = {
                    row["trade_id"]: dict(row)
                    for row in session.execute(statement).mappings().all()
                }

            if signal_ids:
                conditions = [
                    orders.c.user_id == user_id,
                    orders.c.signal_id.in_(set(signal_ids)),
                ]
                if strategy_version is not None:
                    conditions.append(
                        orders.c.strategy_version == int(strategy_version)
                    )
                statement = select(orders).where(*conditions).order_by(orders.c.created_at)
                for row in session.execute(statement).mappings().all():
                    matching_orders.setdefault(row["signal_id"], dict(row))
                episodes, _total = TradeJournalRepository.list_for_user(
                    session,
                    user_id,
                    closed_only=True,
                    limit=None,
                )
        return {
            "backtest_trades": trades,
            "orders": matching_orders,
            "episodes": episodes,
        }

    def backtest_data(
        self, run_id: str, user_id: str
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        """Return a backtest run and its saved trades when the user owns it.

        Args:
            run_id: Run identifier.
            user_id: Account that must own the run.

        Returns:
            The run row and its trades, or ``None`` and an empty list.
        """
        with self._database.session() as session:
            run = BacktestRunRepository.get(session, run_id, user_id)
            if run is None:
                return None, []
            return run, BacktestArtefactRepository.trades(session, run_id)

    def record_observations(
        self, observations: Sequence[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Store learning observations in one database transaction.

        Args:
            observations: Observation values accepted by the SQL repository.

        Returns:
            Stored observation rows in input order.
        """
        with self._database.session() as session:
            return [
                LearningObservationRepository.record(session, **observation)
                for observation in observations
            ]
