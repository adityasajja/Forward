"""Application workflows for maintaining local daily market history."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from forward.core.daily_maintenance_ports import DailyMarketMaintenancePort


class DailyMarketMaintenanceService:
    """Run daily data checks and keep tracked history current."""

    def __init__(self, maintenance: DailyMarketMaintenancePort) -> None:
        """Create the service with the configured market-data adapter.

        Args:
            maintenance: Operations for reading and writing local history files.
        """
        self._maintenance = maintenance

    def check_data(self, data_root: Path) -> dict[str, Any]:
        """Write and return the daily quality report."""
        return self._maintenance.check_data(data_root)

    def sync_exchange_history(self, data_root: Path) -> dict[str, Any] | None:
        """Sync exchange files when due and record a completed sync."""
        if not self._maintenance.market_history_due(data_root):
            return None
        result = self._maintenance.sync_market_history(data_root)
        self._maintenance.mark_market_history_done(data_root)
        return result
