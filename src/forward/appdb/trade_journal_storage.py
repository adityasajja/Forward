"""SQL adapter for trade-journal reconciliation."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from forward.appdb.engine import AppDatabase
from forward.appdb.execution_schema import orders
from forward.appdb.order_event_repository import OrderEventRepository
from forward.appdb.trade_journal_repository import TradeJournalRepository
from forward.core.trade_journal_ports import (
    TradeJournalStoragePort,
)


class SqlTradeJournalStorage(TradeJournalStoragePort):
    """Run journal database operations inside one SQL transaction."""

    def __init__(self, database: AppDatabase) -> None:
        """Create the adapter for one application database.

        Args:
            database: Database that stores orders and journal entries.
        """
        self._database = database

    @contextmanager
    def transaction(self) -> Iterator[SqlTradeJournalUnitOfWork]:
        """Yield a SQL unit of work and commit only when it completes cleanly."""
        with self._database.session() as session:
            yield SqlTradeJournalUnitOfWork(session)


class SqlTradeJournalUnitOfWork:
    """Implement journal operations within the adapter's open transaction."""

    def __init__(self, session: Session) -> None:
        """Create a unit of work for one active SQL session.

        Args:
            session: Session owned by ``SqlTradeJournalStorage``.
        """
        self._session = session

    def entries(self, user_id: str, deployment_id: str) -> list[dict[str, Any]]:
        """Read this deployment's journal rows.

        Args:
            user_id: Account that owns the entries.
            deployment_id: Deployment being reconciled.

        Returns:
            Journal rows returned by the repository.
        """
        entries, _ = TradeJournalRepository.list_for_user(
            self._session, user_id, deployment_id=deployment_id, limit=None
        )
        return entries

    def order_attribution_data(
        self, order_id: str
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        """Read the strategy fields and events needed to grade an order.

        Args:
            order_id: Order whose attribution is needed.

        Returns:
            The order row and ordered transition events. A missing order returns
            ``None`` and an empty event list.
        """
        row = self._session.execute(
            select(orders.c.strategy_id, orders.c.strategy_version).where(
                orders.c.order_id == order_id
            )
        ).mappings().first()
        if row is None:
            return None, []
        order = dict(row)
        return order, OrderEventRepository.history(self._session, order_id)

    def open_entry(
        self,
        *,
        user_id: str,
        symbol: str,
        side: str,
        quantity: float,
        entry_price: float,
        entry_ts: datetime | None,
        deployment_id: str,
        strategy_id: str | None,
        strategy_version: int | None,
        signal_reason: str | None,
        evidence_grade: str | None,
    ) -> dict[str, Any]:
        """Insert an open journal entry.

        Args:
            user_id: Account that owns the entry.
            symbol: Traded instrument.
            side: Position direction.
            quantity: Absolute position size at entry.
            entry_price: Recorded opening fill price.
            entry_ts: Timestamp of the opening fill.
            deployment_id: Deployment that placed the trade.
            strategy_id: Strategy that raised the order, when known.
            strategy_version: Exact strategy version, when known.
            signal_reason: Recorded reason for the entry, when available.
            evidence_grade: Provenance grade from the opening order.

        Returns:
            The inserted journal row.
        """
        return TradeJournalRepository.open_trade(
            self._session,
            user_id=user_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            entry_price=entry_price,
            entry_ts=entry_ts,
            deployment_id=deployment_id,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            signal_reason=signal_reason,
            evidence_grade=evidence_grade,
        )

    def close_entry(
        self,
        trade_id: str,
        user_id: str,
        *,
        exit_price: float,
        gross_pnl: float,
        net_pnl: float,
        exit_ts: datetime,
        mfe: float,
        mae: float,
        slippage_bps: float | None,
        exit_reason: str | None,
    ) -> bool:
        """Close a journal entry and store its reason when present.

        Args:
            trade_id: Entry being closed.
            user_id: Account that owns the entry.
            exit_price: Price of the final fill.
            gross_pnl: P&L before commission.
            net_pnl: P&L after commission.
            exit_ts: Timestamp of the final fill.
            mfe: Maximum favourable excursion in rupees.
            mae: Maximum adverse excursion in rupees.
            slippage_bps: Mean measurable entry and exit slippage.
            exit_reason: Recorded reason from the closing order, when present.

        Returns:
            Whether the entry was updated.
        """
        updated = TradeJournalRepository.close_trade(
            self._session,
            trade_id,
            user_id,
            exit_price=exit_price,
            gross_pnl=gross_pnl,
            net_pnl=net_pnl,
            exit_ts=exit_ts,
            mfe=mfe,
            mae=mae,
            slippage_bps=slippage_bps,
        )
        if updated and exit_reason:
            TradeJournalRepository.set_exit_reason(
                self._session, trade_id, user_id, exit_reason
            )
        return bool(updated)
