"""Ports for aggregating live ticks into chart candles."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol


class TickCandleAggregationPort(Protocol):
    """Aggregate one symbol's ticks into fixed time buckets."""

    def resample_candles(
        self,
        symbol: str,
        ticks: Sequence[dict[str, Any]],
        interval_seconds: int,
    ) -> list[dict[str, Any]]:
        """Return OHLCV candles for the supplied tick sequence."""
