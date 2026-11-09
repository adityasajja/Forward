"""Application use case for local market cache status."""

from __future__ import annotations

from typing import Any

from forward.core.market_data_status_ports import ContractCacheStatusPort, DailyHistoryStatusPort


class MarketCacheStatusService:
    """Read daily history and contract cache coverage."""

    def __init__(
        self,
        daily_history: DailyHistoryStatusPort,
        contracts: ContractCacheStatusPort,
    ) -> None:
        """Create the market cache status service.

        Args:
            daily_history: Adapter that reads daily history coverage.
            contracts: Adapter that reads contract cache coverage.
        """
        self._daily_history = daily_history
        self._contracts = contracts

    def daily_history(self) -> dict[str, Any]:
        """Return daily history cache coverage."""
        return self._daily_history.status()

    def contracts(self) -> dict[str, Any]:
        """Return contract cache coverage."""
        return self._contracts.status()
