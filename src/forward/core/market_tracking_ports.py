"""Ports for market data used by forward tracking."""

from __future__ import annotations

from datetime import date
from typing import Any, Protocol


class TrackRecordMarketDataPort(Protocol):
    """Read daily prices and source details for paper track records."""

    def stock_closes(self, symbol: str, exchange: str) -> dict[date, float] | None:
        """Return valid daily closes for one listed instrument.

        Args:
            symbol: Trading symbol held by a paper deployment.
            exchange: Exchange segment for the instrument.

        Returns:
            Closes by market date, or None when history is unavailable.
        """

    def benchmark(self) -> tuple[dict[date, float] | None, dict[str, Any]]:
        """Return benchmark closes and their source details.

        Returns:
            Daily closes and source details, or empty data when unavailable.
        """

    def is_demo(self) -> bool:
        """Return whether the configured market data is marked as demo."""


class VwapLiquidityPort(Protocol):
    """Read prior-session liquidity inputs for the VWAP paper strategy."""

    def liquidity(self, day: date) -> dict[str, dict[str, Any]]:
        """Return average liquidity known before the requested day.

        Args:
            day: Session date that the strategy is evaluating.

        Returns:
            Symbol-keyed volume, value, close, and source-date measurements.
        """
