"""Ports for journal reconciliation and its application service."""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime
from typing import Any, Protocol


class TradeJournalServicePort(Protocol):
    """Reconcile journal episodes from the current account book."""

    def reconcile(self, user_id: str, deployment_id: str) -> dict[str, Any]:
        """Reconcile one deployment.

        Args:
            user_id: Account that owns the deployment.
            deployment_id: Deployment to reconcile.

        Returns:
            Journal changes from this pass.
        """


class TradeJournalUnitOfWorkPort(Protocol):
    """Read and update journal rows in one transaction."""

    def entries(self, user_id: str, deployment_id: str) -> list[dict[str, Any]]:
        """Read a deployment's journal entries in the declared order.

        Args:
            user_id: Account that owns the entries.
            deployment_id: Deployment being reconciled.

        Returns:
            The journal rows.
        """

    def order_attribution_data(
        self, order_id: str
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        """Read an order and its ordered status events.

        Args:
            order_id: Order whose strategy and provenance are needed.

        Returns:
            The order row and its events. The order row is ``None`` when missing.
        """

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
        """Record an open trade.

        Args:
            user_id: Account that owns the trade.
            symbol: Traded instrument.
            side: Position direction.
            quantity: Absolute size at entry.
            entry_price: Opening fill price.
            entry_ts: Opening fill time.
            deployment_id: Deployment that placed the trade.
            strategy_id: Strategy that raised the order, if known.
            strategy_version: Strategy version, if known.
            signal_reason: Recorded entry reason, if available.
            evidence_grade: Provenance from the opening order.

        Returns:
            The inserted journal entry.
        """

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
        """Close a journal entry.

        Args:
            trade_id: Trade to close.
            user_id: Account that owns the trade.
            exit_price: Closing fill price.
            gross_pnl: Profit or loss before costs.
            net_pnl: Profit or loss after costs.
            exit_ts: Closing fill time.
            mfe: Maximum favorable excursion.
            mae: Maximum adverse excursion.
            slippage_bps: Measured slippage, when available.
            exit_reason: Recorded reason for closing.

        Returns:
            Whether a journal entry was closed.
        """


class TradeJournalStoragePort(Protocol):
    """Open journal transactions for trade reconciliation."""

    def transaction(self) -> AbstractContextManager[TradeJournalUnitOfWorkPort]:
        """Open one transaction for a reconciliation pass."""
