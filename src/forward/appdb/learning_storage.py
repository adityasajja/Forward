"""SQL adapter for the learning service's stored evidence."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select

from forward.appdb.engine import AppDatabase
from forward.appdb.execution_schema import deployments, orders
from forward.appdb.learning_observation_repository import LearningObservationRepository
from forward.appdb.signal_context_repository import SignalContextRepository
from forward.appdb.strategy_backtest_schema import backtest_runs, backtest_trades
from forward.appdb.trade_attribution_repository import TradeAttributionRepository
from forward.appdb.trade_schema import signal_contexts, trade_journal
from forward.core.learning_ports import (
    LearningStoragePort,
)


class SqlLearningStorage(LearningStoragePort):
    """Read learning evidence through the application's SQL repositories."""

    def __init__(self, database: AppDatabase) -> None:
        """Create the adapter for one application database.

        Args:
            database: Database that owns the learning records.
        """
        self._database = database

    def completed_backtest_runs(self, user_id: str | None, limit: int) -> list[dict[str, Any]]:
        """Read completed backtests, newest first.

        Args:
            user_id: Account that owns the runs, or ``None`` for all accounts.
            limit: Maximum number of runs to return.

        Returns:
            Completed run rows.
        """
        conditions = [backtest_runs.c.status == "COMPLETED"]
        if user_id:
            conditions.append(backtest_runs.c.user_id == user_id)
        statement = (
            select(backtest_runs)
            .where(*conditions)
            .order_by(backtest_runs.c.created_at.desc())
            .limit(max(1, min(limit, 2_000)))
        )
        with self._database.session() as session:
            return [dict(row) for row in session.execute(statement).mappings()]

    def backtest_trades(self, run_ids: Sequence[str]) -> list[dict[str, Any]]:
        """Read trades for selected backtest runs.

        Args:
            run_ids: Run identifiers to read.

        Returns:
            Trade rows ordered by run and sequence.
        """
        if not run_ids:
            return []
        statement = (
            select(backtest_trades)
            .where(backtest_trades.c.run_id.in_(list(run_ids)))
            .order_by(backtest_trades.c.run_id, backtest_trades.c.seq)
        )
        with self._database.session() as session:
            return [dict(row) for row in session.execute(statement).mappings()]

    def journal_rows(self, user_id: str | None, limit: int) -> list[dict[str, Any]]:
        """Read journal episodes in entry-time order.

        Args:
            user_id: Account that owns the episodes, or ``None`` for all accounts.
            limit: Maximum number of episodes to return.

        Returns:
            Journal rows.
        """
        statement = select(trade_journal)
        if user_id:
            statement = statement.where(trade_journal.c.user_id == user_id)
        statement = statement.order_by(trade_journal.c.entry_ts).limit(max(1, min(limit, 200_000)))
        with self._database.session() as session:
            return [dict(row) for row in session.execute(statement).mappings()]

    def deployment_modes(self, user_id: str | None) -> dict[str, str]:
        """Read deployment modes keyed by deployment id.

        Returns:
            Deployment id to upper-case mode mapping.
        """
        statement = select(deployments.c.deployment_id, deployments.c.mode)
        if user_id is not None:
            statement = statement.where(deployments.c.user_id == user_id)
        with self._database.session() as session:
            return {
                row["deployment_id"]: row["mode"].upper()
                for row in session.execute(statement).mappings()
            }

    def trade_attributions(
        self, user_id: str, trade_ids: Sequence[str]
    ) -> dict[str, dict[str, Any]]:
        """Read saved attributions for selected trades.

        Args:
            user_id: Account that owns the trades.
            trade_ids: Trade identifiers to read.

        Returns:
            Attribution rows keyed by trade id.
        """
        if not trade_ids:
            return {}
        with self._database.session() as session:
            return TradeAttributionRepository.for_trades(session, user_id, list(trade_ids))

    def recorded_signal_contexts(
        self, user_id: str | None
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Read orders and contexts used to link journal episodes.

        Args:
            user_id: Account that owns the records, or ``None`` for all accounts.

        Returns:
            Ordered orders and their recorded signal contexts.
        """
        order_conditions = [orders.c.signal_id.is_not(None)]
        context_conditions = []
        if user_id:
            order_conditions.append(orders.c.user_id == user_id)
            context_conditions.append(signal_contexts.c.user_id == user_id)
        order_statement = (
            select(
                orders.c.order_id,
                orders.c.signal_id,
                orders.c.symbol,
                orders.c.side,
                orders.c.deployment_id,
                orders.c.strategy_id,
                orders.c.created_at,
            )
            .where(*order_conditions)
            .order_by(orders.c.created_at)
            .limit(50_000)
        )
        context_statement = select(
            signal_contexts.c.signal_id,
            signal_contexts.c.context_score,
            signal_contexts.c.context_model_version,
            signal_contexts.c.context_class,
        ).where(*context_conditions)
        with self._database.session() as session:
            order_rows = [dict(row) for row in session.execute(order_statement).mappings()]
            context_rows = [dict(row) for row in session.execute(context_statement).mappings()]
        return order_rows, context_rows

    def evidence_counts(self, forward_grade: str, user_id: str | None) -> dict[str, int]:
        """Count stored backtest and journal evidence.

        Args:
            forward_grade: Stored grade that represents forward evidence.
            user_id: Account that owns the evidence, or None for a platform report.

        Returns:
            Counts used by the learning status view.
        """
        with self._database.session() as session:
            backtest_trade_count = select(func.count()).select_from(
                backtest_trades.join(backtest_runs)
            )
            journal_count = select(func.count()).select_from(trade_journal)
            forward_journal_count = (
                select(func.count())
                .select_from(trade_journal)
                .where(trade_journal.c.evidence_grade == forward_grade)
            )
            completed_run_count = (
                select(func.count())
                .select_from(backtest_runs)
                .where(backtest_runs.c.status == "COMPLETED")
            )
            run_count = select(func.count()).select_from(backtest_runs)
            if user_id is not None:
                backtest_trade_count = backtest_trade_count.where(
                    backtest_runs.c.user_id == user_id
                )
                journal_count = journal_count.where(trade_journal.c.user_id == user_id)
                forward_journal_count = forward_journal_count.where(
                    trade_journal.c.user_id == user_id
                )
                completed_run_count = completed_run_count.where(
                    backtest_runs.c.user_id == user_id
                )
                run_count = run_count.where(backtest_runs.c.user_id == user_id)
            return {
                "backtest_trades": int(
                    session.execute(backtest_trade_count).scalar() or 0
                ),
                "trade_journal": int(
                    session.execute(journal_count).scalar() or 0
                ),
                "trade_journal_forward": int(
                    session.execute(forward_journal_count).scalar() or 0
                ),
                "completed_runs": int(
                    session.execute(completed_run_count).scalar() or 0
                ),
                "backtest_runs": int(
                    session.execute(run_count).scalar() or 0
                ),
            }

    def signal_context_count(self, user_id: str, strategy_id: str) -> int:
        """Count recorded signal contexts for one strategy.

        Args:
            user_id: Account that owns the contexts.
            strategy_id: Strategy whose contexts are counted.

        Returns:
            Number of recorded contexts.
        """
        with self._database.session() as session:
            return SignalContextRepository.count(session, user_id, strategy_id=strategy_id)

    def list_observations(
        self,
        *,
        user_id: str | None,
        strategy_id: str | None,
        strategy_version: int | None,
        evidence_class: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Read learning observations using the requested filters.

        Args:
            strategy_id: Optional strategy filter.
            strategy_version: Optional version filter.
            evidence_class: Optional evidence-class filter.
            limit: Maximum number of rows to return.

        Returns:
            Matching observation rows.
        """
        with self._database.session() as session:
            return LearningObservationRepository.list_observations(
                session,
                user_id=user_id,
                strategy_id=strategy_id,
                strategy_version=strategy_version,
                evidence_class=evidence_class,
                limit=limit,
            )

    def observation_counts_by_strategy(
        self, evidence_classes: Sequence[str], user_id: str
    ) -> dict[str, int]:
        """Count all observations in the requested evidence classes.

        Args:
            evidence_classes: Evidence classes whose observations are counted.

        Returns:
            Complete counts keyed by strategy ID.
        """
        with self._database.session() as session:
            return LearningObservationRepository.observation_counts_by_strategy(
                session, tuple(evidence_classes), user_id
            )
