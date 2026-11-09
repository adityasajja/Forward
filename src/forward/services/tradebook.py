"""Capture each day's broker trades and positions."""

from __future__ import annotations

from datetime import date, datetime

from forward.core.session import IST
from forward.core.tradebook_ports import DailyTradebookStoragePort, TradebookReadPort


class TradebookCaptureService:
    """Save raw broker rows before the broker's daily records reset."""

    def __init__(
        self,
        broker: TradebookReadPort,
        storage: DailyTradebookStoragePort,
    ) -> None:
        """Create the capture service.

        Args:
            broker: Port that reads daily trade and position rows.
            storage: Port that saves rows by market day.
        """
        self._broker = broker
        self._storage = storage

    def capture(self, day: date | None = None) -> dict[str, int]:
        """Save the day's trades, then positions, and return saved row counts.

        Args:
            day: Market day to save. Uses the current IST date when omitted.

        Returns:
            Number of saved trades and positions.
        """
        if day is None:
            day = datetime.now(IST).date()
        trades = self._broker.trades()
        if trades:
            self._storage.save_trades(day, trades)

        positions = self._broker.positions()
        if positions:
            self._storage.save_positions(day, positions)

        return {"trades": len(trades), "positions": len(positions)}
