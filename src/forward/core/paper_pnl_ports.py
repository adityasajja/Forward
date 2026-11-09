"""Ports for paper pnl."""

from __future__ import annotations

from typing import Any, Protocol


class PaperPnlStoragePort(Protocol):
    """Read closed paper trades for the P&L calendar."""

    def closed_paper_trade_pnl(self, user_id: str) -> list[tuple[str, float]]:
        """Return one user's closed paper trades by exit date and net P&L.

        Args:
            user_id: Owner of the trades to read.
        """

    def closed_paper_trades_on(self, user_id: str, day: str) -> list[dict[str, Any]]:
        """Return one user's closed paper trades for an ISO calendar day.

        Args:
            user_id: Owner of the trades to read.
            day: ISO calendar date.
        """
