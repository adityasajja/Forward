"""Ports for scheduled daily market-data maintenance."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Protocol


class DailyMarketMaintenancePort(Protocol):
    """Read and update the local market-history files used by Forward."""

    def check_data(self, data_root: Path) -> dict[str, Any]:
        """Scan cached daily bars for gaps, jumps, and unreadable files."""

    def market_history_due(self, data_root: Path) -> bool:
        """Return whether exchange daily history should be synchronized."""

    def sync_market_history(self, data_root: Path) -> dict[str, Any]:
        """Download and rebuild supported exchange history."""

    def mark_market_history_done(self, data_root: Path, now: datetime | None = None) -> None:
        """Record the date of a completed exchange-history sync."""
