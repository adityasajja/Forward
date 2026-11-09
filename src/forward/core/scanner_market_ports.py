"""Port for scanner market data."""

from __future__ import annotations

from typing import Any, Protocol


class ScannerMarketPort(Protocol):
    """Read daily bars for exact instruments used by market scans."""

    def daily_bars(
        self,
        symbol: str,
        exchange: str,
        from_date: str,
        to_date: str,
    ) -> Any:
        """Return daily OHLCV bars for one contract.

        Args:
            symbol: Exact trading symbol from the instrument catalog.
            exchange: Exchange segment containing the contract.
            from_date: First requested date in broker format.
            to_date: Last requested date in broker format.

        Returns:
            Daily bars with timestamp, OHLC, and volume fields.
        """
