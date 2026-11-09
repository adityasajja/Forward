"""Local file adapters for scheduled market-data maintenance."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

from forward.core.daily_maintenance_ports import DailyMarketMaintenancePort
from forward.data import market_history, quality


class LocalDailyMarketMaintenanceAdapter(DailyMarketMaintenancePort):
    """Connect market-maintenance workflows to their local file modules."""

    def __init__(
        self,
        *,
        request_timeout_seconds: float,
        start_date: date,
        workers: int,
    ) -> None:
        """Create the adapter with explicit exchange-history settings.

        Args:
            request_timeout_seconds: Maximum time for each exchange request.
            start_date: First date to include in the history sync.
            workers: Number of concurrent exchange downloads.
        """
        self._request_timeout_seconds = request_timeout_seconds
        self._start_date = start_date
        self._workers = workers

    def check_data(self, data_root: Path) -> dict[str, Any]:
        """Run the saved daily-data quality scan."""
        return quality.run_daily(data_root)

    def market_history_due(self, data_root: Path) -> bool:
        """Check whether exchange history needs a new daily sync."""
        return market_history.due(data_root)

    def sync_market_history(self, data_root: Path) -> dict[str, Any]:
        """Download and rebuild BSE and NSE SME history."""
        return market_history.sync(
            data_root,
            request_timeout_seconds=self._request_timeout_seconds,
            start=self._start_date,
            workers=self._workers,
        )

    def mark_market_history_done(
        self, data_root: Path, now: datetime | None = None
    ) -> None:
        """Write the completed exchange-history sync marker."""
        market_history.mark_done(data_root, now)
