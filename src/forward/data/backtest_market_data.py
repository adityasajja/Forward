"""Read cached frames and build the feed used by a backtest."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import polars as pl
from loguru import logger

from forward.core.backtest_ports import (
    BacktestFeedPort,
    BacktestMarketDataPort,
)
from forward.core.enums import Timeframe
from forward.core.feeds import ListFeed
from forward.core.historical_market_data_ports import (
    DailyBarCachePort,
    HistoricalInstrumentCatalogPort,
)
from forward.core.instrument_models import Instrument
from forward.core.symbols import canonical_symbol
from forward.data.base import pivot_to_snapshots
from forward.data.history import DAILY_DIR
from forward.data.intraday_history import load_intraday


class CachedBacktestMarketDataAdapter(BacktestMarketDataPort):
    """Load backtest history from Forward's daily and intraday caches.

    Args:
        catalog: Catalog of canonical symbols and cache files.
        daily_cache: Shared cache reader for per-symbol bars.
        cache_root: Root folder that contains daily and intraday caches.
    """

    def __init__(
        self,
        catalog: HistoricalInstrumentCatalogPort,
        daily_cache: DailyBarCachePort,
        cache_root: Path,
    ) -> None:
        self._catalog = catalog
        self._daily_cache = daily_cache
        self._cache_root = Path(cache_root)

    def load_frames(
        self,
        symbols: Sequence[str],
        exchange: str,
        timeframe: str,
        end: str | None,
    ) -> tuple[Mapping[str, Any], list[str]]:
        """Load the longest available cache file for each requested symbol.

        Args:
            symbols: Trading symbols requested by the run.
            exchange: Exchange segment for the requested data.
            timeframe: Candle size selected for the run.
            end: Optional inclusive final market date.

        Returns:
            Frames keyed by canonical symbol and any adapter warnings.
        """
        if timeframe != Timeframe.DAY_1.value:
            frames = load_intraday(
                self._cache_root,
                list(symbols),
                exchange,
                timeframe,
                end,
            )
            return frames, []

        outdir = self._cache_root / DAILY_DIR / exchange.upper()
        candidates: dict[str, list[str]] = {}
        for symbol in sorted({item.upper() for item in symbols}):
            record = self._catalog.get(symbol)
            cache_file = record.cache_file if record is not None else None
            stems = [Path(cache_file).stem] if cache_file else []
            if symbol not in stems:
                stems.append(symbol)
            candidates[symbol] = stems

        frames: dict[str, Any] = {}
        filenames = list(dict.fromkeys(stem for stems in candidates.values() for stem in stems))
        cached_frames = self._daily_cache.load(outdir, filenames)
        for symbol, stems in candidates.items():
            best: tuple[str, Any] | None = None
            for stem in stems:
                frame = cached_frames.get(stem)
                if frame is None:
                    continue
                filename = f"{stem}.parquet"
                if best is None or len(frame) > len(best[1]):
                    best = (filename, frame)

            if best is None:
                continue
            key = canonical_symbol(symbol) or symbol
            frames[key] = best[1]

        if frames:
            logger.debug("loaded {} frames: {}", len(frames), sorted(frames))
        return frames, []

    def build_feed(
        self,
        frames: Mapping[str, Any],
        exchange: str,
        timeframe: str,
    ) -> BacktestFeedPort:
        """Convert frames into snapshots with the matching instruments.

        Args:
            frames: Clipped and warmup-aware frames keyed by symbol.
            exchange: Exchange segment for the instruments.
            timeframe: Candle size represented by the frames.

        Returns:
            A feed ready for the backtest engine.
        """
        return frames_to_backtest_feed(frames, exchange, timeframe)


def frames_to_backtest_feed(
    frames: Mapping[str, Any], exchange: str, timeframe: str
) -> BacktestFeedPort:
    """Convert symbol frames into ordered snapshots for the backtest engine.

    Args:
        frames: Clipped and warmup-aware frames keyed by symbol.
        exchange: Exchange segment for the instruments.
        timeframe: Candle size represented by the frames.

    Returns:
        A feed ready for the backtest engine.
    """
    instruments = {
        symbol: Instrument(symbol=symbol, exchange=exchange.upper())
        for symbol in frames
    }
    combined = pl.concat(
        [
            pl.from_pandas(frame.reset_index().rename(columns={"index": "ts"}))
            .with_columns(pl.lit(symbol).alias("symbol"))
            for symbol, frame in frames.items()
        ],
        how="diagonal_relaxed",
    )
    snapshots = pivot_to_snapshots(combined, Timeframe(timeframe))
    return ListFeed(snapshots, instruments)
