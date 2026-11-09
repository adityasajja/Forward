"""Ports for broker tradebooks and their local storage."""

from __future__ import annotations

from datetime import date
from typing import Any, Protocol


class TradebookReadPort(Protocol):
    """Read today's trades and positions from a broker."""

    def trades(self) -> list[dict[str, Any]]:
        """Return today's raw trade rows."""

    def positions(self) -> list[dict[str, Any]]:
        """Return today's raw position rows."""


class DailyTradebookStoragePort(Protocol):
    """Store raw broker rows by market day."""

    def save_trades(self, day: date, rows: list[dict[str, Any]]) -> None:
        """Save one day's trade rows.

        Args:
            day: Market day for the rows.
            rows: Raw broker trade records.
        """

    def save_positions(self, day: date, rows: list[dict[str, Any]]) -> None:
        """Save one day's position rows.

        Args:
            day: Market day for the rows.
            rows: Raw broker position records.
        """
