"""Ports for live market data."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any, Protocol


class LiveTickSnapshot(Protocol):
    """Expose tick fields used by live paper strategies."""

    ts: str
    epoch: float
    open: float
    close: float | None
    best_bid: float | None
    best_ask: float | None


class LiveTickPricePort(Protocol):
    """Read the latest price and tick for one market symbol."""

    def latest_price_and_time(self, symbol: str) -> tuple[float | None, datetime | None]:
        """Return the last price and its exchange timestamp."""

    def latest_tick(self, symbol: str) -> LiveTickSnapshot | None:
        """Return the latest tick, or None before the first tick."""


class MarketDataPort(LiveTickPricePort, Protocol):
    """Read live ticks and manage server-side market data subscriptions."""

    def ensure_indices(self, indices: tuple[tuple[str, str], ...] | None = None) -> bool:
        """Subscribe to the requested index topics."""

    def ensure_symbols(
        self,
        symbols: Iterable[str] | Mapping[str, Iterable[str]],
        exchange: str = "NSEEQ",
    ) -> dict[str, list[str]]:
        """Subscribe to the target symbols and report subscription changes."""

    def opening_snapshot(self, day: str) -> dict[str, Any]:
        """Return opening prices captured for one market day."""

    def observed_bars(self, now: datetime) -> dict[str, Any]:
        """Return completed bars captured up to the given time."""
