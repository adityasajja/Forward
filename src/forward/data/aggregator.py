"""Tick → bar aggregation for live trading and for building custom timeframes
from a finer base series."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from forward.core.market_models import Bar


@dataclass
class BarBuilder:
    """Accumulates ticks into a single bar."""

    ts: datetime
    open: float = 0.0
    high: float = float("-inf")
    low: float = float("inf")
    close: float = 0.0
    volume: float | None = 0.0
    _started: bool = False

    def update(self, price: float, size: float | None = None) -> None:
        """Add one observed tick to the bar.

        Args:
            price: Tick price.
            size: Measured tick size, or ``None`` when the feed omits it.

        Raises:
            ValueError: If the price or measured size is invalid.
        """
        if not math.isfinite(price) or price <= 0:
            raise ValueError("tick price must be finite and positive")
        if size is not None and (not math.isfinite(size) or size < 0):
            raise ValueError("tick size must be finite and non-negative when present")
        if not self._started:
            self.open = price
            self._started = True
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        if size is None:
            self.volume = None
        elif self.volume is not None:
            self.volume += size

    def to_bar(self) -> Bar:
        return Bar(
            ts=self.ts,
            open=self.open,
            high=self.high if self._started else self.close,
            low=self.low if self._started else self.close,
            close=self.close,
            volume=self.volume,
        )


class TickToBarAggregator:
    """Buckets a stream of (timestamp, price, size) into fixed-width bars.

    Emits a bar when a tick arrives in a later bucket, or on :meth:`flush`.
    """

    def __init__(self, freq: str = "1min", on_bar=None) -> None:
        self.freq = freq
        self.on_bar = on_bar
        self._current: BarBuilder | None = None
        self._bucket: datetime | None = None

    def update(
        self, ts: datetime, price: float, size: float | None = None
    ) -> Bar | None:
        """Add a tick and return the prior bar when a new time bucket starts.

        Args:
            ts: Tick timestamp.
            price: Tick price.
            size: Measured tick size, or ``None`` when the feed omits it.

        Returns:
            The completed prior bar, or ``None`` when this tick stays in the
            current bucket.
        """
        bucket = pd.Timestamp(ts).floor(self.freq).to_pydatetime()
        completed = None
        if self._bucket is not None and bucket > self._bucket:
            completed = self._current.to_bar() if self._current else None
            self._current = None
        if self._current is None:
            self._bucket = bucket
            self._current = BarBuilder(ts=bucket)
        self._current.update(price, size)
        if completed is not None and self.on_bar:
            self.on_bar(completed)
        return completed

    def current_bar(self) -> Bar | None:
        """The in-progress bar (for dashboards / latest-price lookups)."""
        return self._current.to_bar() if self._current else None

    def flush(self) -> Bar | None:
        if self._current is None:
            return None
        bar = self._current.to_bar()
        self._current = None
        self._bucket = None
        return bar
