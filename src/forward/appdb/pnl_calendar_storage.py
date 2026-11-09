"""SQL adapter for closed paper trades shown by the P&L calendar."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import and_, select

from forward.appdb.engine import AppDatabase
from forward.appdb.execution_schema import deployments
from forward.appdb.strategy_backtest_schema import strategies
from forward.appdb.trade_schema import trade_journal
from forward.core.paper_pnl_ports import PaperPnlStoragePort


class SqlPaperPnlStorage(PaperPnlStoragePort):
    """Read closed paper trades from the application database."""

    def __init__(self, database: AppDatabase) -> None:
        """Create the adapter for one application database.

        Args:
            database: Database that owns the SQL session lifecycle.
        """
        self._database = database

    def closed_paper_trade_pnl(self, user_id: str) -> list[tuple[str, float]]:
        """Return exit dates and net P&L for one user's closed paper trades.

        Args:
            user_id: Owner of the trades to read.

        Returns:
            Each closed paper trade's ISO exit date and net P&L in rupees.
        """
        statement = (
            select(
                trade_journal.c.trade_id,
                trade_journal.c.exit_ts,
                trade_journal.c.net_pnl,
            )
            .join(deployments, deployments.c.deployment_id == trade_journal.c.deployment_id)
            .where(
                deployments.c.mode == "PAPER",
                deployments.c.user_id == user_id,
                trade_journal.c.user_id == user_id,
                trade_journal.c.exit_ts.is_not(None),
            )
        )
        with self._database.session() as session:
            rows = session.execute(statement).all()
        return [
            (str(exit_ts)[:10], self._net_pnl(trade_id, net_pnl))
            for trade_id, exit_ts, net_pnl in rows
        ]

    def closed_paper_trades_on(self, user_id: str, day: str) -> list[dict[str, Any]]:
        """Return detail fields for one user's trades closed on one day.

        Args:
            user_id: Owner of the trades to read.
            day: ISO calendar date.

        Returns:
            Closed trades for the requested day, with strategy names.
        """
        start = datetime.combine(date.fromisoformat(day), datetime.min.time())
        end = start + timedelta(days=1)
        statement = (
            select(
                trade_journal.c.symbol,
                trade_journal.c.side,
                trade_journal.c.quantity,
                trade_journal.c.entry_price,
                trade_journal.c.exit_price,
                trade_journal.c.exit_ts,
                trade_journal.c.trade_id,
                trade_journal.c.net_pnl,
                trade_journal.c.exit_reason,
                strategies.c.name.label("strategy_name"),
            )
            .select_from(
                trade_journal.join(
                    deployments,
                    deployments.c.deployment_id == trade_journal.c.deployment_id,
                ).outerjoin(
                    strategies,
                    and_(
                        strategies.c.strategy_id == trade_journal.c.strategy_id,
                        strategies.c.user_id == user_id,
                    ),
                )
            )
            .where(
                deployments.c.mode == "PAPER",
                deployments.c.user_id == user_id,
                trade_journal.c.user_id == user_id,
                trade_journal.c.exit_ts >= start,
                trade_journal.c.exit_ts < end,
            )
        )
        with self._database.session() as session:
            rows = session.execute(statement).mappings().all()
        return [
            {
                "source": row["strategy_name"],
                "symbol": row["symbol"],
                "side": row["side"],
                "quantity": row["quantity"],
                "entry": row["entry_price"],
                "exit": row["exit_price"],
                "pnl": round(self._net_pnl(row["trade_id"], row["net_pnl"]), 2),
                "pnl_pct": (
                    round(
                        100
                        * self._net_pnl(row["trade_id"], row["net_pnl"])
                        / (row["entry_price"] * row["quantity"]),
                        2,
                    )
                    if row["entry_price"] and row["quantity"]
                    else None
                ),
                "note": row["exit_reason"],
            }
            for row in rows
        ]

    @staticmethod
    def _net_pnl(trade_id: str, value: float | None) -> float:
        """Require the stored net P&L instead of substituting a gross value."""
        if value is None:
            raise ValueError(f"Closed paper trade {trade_id} has no net P&L")
        return float(value)
