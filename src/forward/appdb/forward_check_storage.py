"""SQL adapter for backtest and forward-check trade reads."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from forward.appdb.engine import AppDatabase
from forward.appdb.strategy_backtest_schema import backtest_runs, backtest_trades
from forward.appdb.trade_schema import trade_journal
from forward.core.forward_tracking_ports import (
    ForwardCheckStoragePort,
)


class SqlForwardCheckStorage(ForwardCheckStoragePort):
    """Read strategy comparison data from the application database."""

    def __init__(self, database: AppDatabase) -> None:
        """Create the adapter for one application database.

        Args:
            database: Database that owns the SQL session lifecycle.
        """
        self._database = database

    def latest_backtest_trades(
        self, user_id: str, strategy_id: str, version: int | None
    ) -> tuple[str | None, list[dict[str, Any]]]:
        """Return trades from the latest completed matching backtest.

        Args:
            user_id: Account that owns the strategy and run.
            strategy_id: Strategy being checked.
            version: Exact strategy version, or ``None`` for any version.

        Returns:
            The latest run ID and its closed trade rows, or ``(None, [])``.
        """
        statement = select(backtest_runs).where(
            backtest_runs.c.user_id == user_id,
            backtest_runs.c.strategy_id == strategy_id,
            backtest_runs.c.status == "COMPLETED",
        )
        if version is not None:
            statement = statement.where(backtest_runs.c.strategy_version == version)

        with self._database.session() as session:
            run = (
                session.execute(statement.order_by(backtest_runs.c.created_at.desc()).limit(1))
                .mappings()
                .first()
            )
            if run is None:
                return None, []

            trades = session.execute(
                select(backtest_trades.c.return_pct, backtest_trades.c.net_pnl).where(
                    backtest_trades.c.run_id == run["run_id"],
                    backtest_trades.c.exit_ts.is_not(None),
                )
            ).all()
        return str(run["run_id"]), [
            {"return_pct": row.return_pct, "net_pnl": row.net_pnl} for row in trades
        ]

    def forward_trades(self, user_id: str, strategy_id: str) -> list[dict[str, Any]]:
        """Return forward journal rows for one account strategy.

        Args:
            user_id: Account that owns the strategy trades.
            strategy_id: Strategy whose forward rows are requested.

        Returns:
            Journal rows ordered by entry time.
        """
        statement = select(trade_journal).where(trade_journal.c.strategy_id == strategy_id)
        statement = statement.where(trade_journal.c.user_id == user_id)
        statement = statement.order_by(trade_journal.c.entry_ts)
        with self._database.session() as session:
            return [dict(row) for row in session.execute(statement).mappings()]
