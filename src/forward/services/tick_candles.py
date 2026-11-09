"""Application service for live tick-candle aggregation."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from forward.core.tick_candle_ports import TickCandleAggregationPort


class TickCandleService:
    """Aggregate live ticks through the configured candle port."""

    def __init__(
        self, aggregator_provider: Callable[[], TickCandleAggregationPort]
    ) -> None:
        """Create the service with an aggregator provider.

        Args:
            aggregator_provider: Returns an aggregator for the current thread.
        """
        self._aggregator_provider = aggregator_provider

    def resample(
        self,
        symbol: str,
        ticks: Sequence[dict[str, Any]],
        interval_seconds: int,
    ) -> list[dict[str, Any]]:
        """Return OHLCV candles for the supplied ticks.

        Args:
            symbol: Canonical instrument symbol.
            ticks: Ordered observations from the live ring buffer.
            interval_seconds: Width of each candle in seconds.

        Returns:
            Candles produced by the configured aggregation port.
        """
        return self._aggregator_provider().resample_candles(
            symbol, ticks, interval_seconds
        )
