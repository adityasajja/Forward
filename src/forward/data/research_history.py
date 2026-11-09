"""Build research feeds from available local daily bars."""

from __future__ import annotations

from collections.abc import Sequence

import polars as pl

from forward.core.backtest_ports import (
    BacktestFeedPort,
    CachedResearchHistoryPort,
)
from forward.core.enums import Timeframe
from forward.core.feeds import ListFeed
from forward.core.instrument_models import Instrument
from forward.data.base import pivot_to_snapshots
from forward.data.history import CACHE_ROOT, load_cached


class CachedResearchHistoryAdapter(CachedResearchHistoryPort):
    """Create daily feeds from only the requested symbols present in cache."""

    def load_cached_daily_feed(
        self, symbols: Sequence[str], exchange: str
    ) -> tuple[BacktestFeedPort, list[str]]:
        """Build a feed from available cached symbols.

        Args:
            symbols: Exact trading symbols to load.
            exchange: Exchange segment that stores the bars.

        Returns:
            The feed and sorted symbols included in it.

        Raises:
            FileNotFoundError: If the exchange cache does not exist.
            LookupError: If none of the requested symbols has cached bars.
        """
        requested = list(dict.fromkeys(symbol.strip().upper() for symbol in symbols))
        frames = load_cached(exchange, requested)
        if not frames:
            if not (CACHE_ROOT / exchange).is_dir():
                raise FileNotFoundError(exchange)
            raise LookupError(exchange)

        combined = pl.concat(
            [
                pl.from_pandas(frame).with_columns(pl.lit(symbol).alias("symbol"))
                for symbol, frame in frames.items()
            ],
            how="diagonal_relaxed",
        )
        snapshots = pivot_to_snapshots(combined, Timeframe.DAY_1)
        instruments = {
            symbol: Instrument(symbol=symbol, exchange=exchange)
            for symbol in frames
        }
        return ListFeed(snapshots, instruments), sorted(frames)
