"""In-memory market feeds shared by backtests and research."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from forward.core.instrument_models import Instrument
from forward.core.market_models import MarketSnapshot


def snapshot_rows(snapshots: Sequence[MarketSnapshot]) -> list[dict[str, Any]]:
    """Flatten snapshots into rows without depending on a dataframe library.

    Args:
        snapshots: Market observations with per-symbol bars.

    Returns:
        Long rows with timestamp, symbol, OHLC, and volume values.
    """
    return [
        {
            "ts": snapshot.ts,
            "symbol": symbol,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
        }
        for snapshot in snapshots
        for symbol, bar in snapshot.bars.items()
    ]


class ListFeed:
    """Provide a fixed set of snapshots to a backtest.

    Args:
        snapshots: Market observations ordered by timestamp.
        instruments: Instruments available to the strategy.
    """

    def __init__(
        self,
        snapshots: Sequence[MarketSnapshot],
        instruments: Mapping[str, Instrument],
    ) -> None:
        self._snapshots = snapshots
        self._instruments = instruments

    @property
    def instruments(self) -> Mapping[str, Instrument]:
        """Return instruments keyed by canonical symbol."""
        return self._instruments

    def load(self) -> Sequence[MarketSnapshot]:
        """Return the feed's market observations."""
        return self._snapshots
