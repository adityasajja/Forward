"""Build backtest feeds from the configured local market-history cache."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import polars as pl

from forward.core.enums import AssetClass, Timeframe
from forward.core.feeds import ListFeed
from forward.core.historical_market_data_ports import (
    DailyBarCachePort,
    HistoricalInstrumentCatalogPort,
    HistoricalInstrumentRecord,
)
from forward.core.instrument_models import Instrument
from forward.data.base import pivot_to_snapshots


class CachedHistoricalFeedAdapter:
    """Load explicitly selected instruments from the local daily cache."""

    def __init__(
        self,
        catalog: HistoricalInstrumentCatalogPort,
        bar_cache: DailyBarCachePort,
        daily_cache_root: Path,
    ) -> None:
        """Create the adapter with its instrument and bar-reading ports.

        Args:
            catalog: Port that resolves instruments and named universes.
            bar_cache: Port that reads daily bars from local storage.
            daily_cache_root: Root directory containing exchange cache folders.
        """
        self._catalog = catalog
        self._bar_cache = bar_cache
        self._daily_cache_root = daily_cache_root

    def load(
        self,
        *,
        exchange: str | None,
        symbols: Sequence[str] | None,
        universe: str | None,
    ) -> ListFeed:
        """Create an in-memory feed from configured real historical bars.

        Args:
            exchange: Exchange code, or ``None`` when symbols identify it.
            symbols: Explicit symbols to load.
            universe: Stored universe to load when symbols are not supplied.

        Returns:
            A feed containing every requested instrument.

        Raises:
            LookupError: The selection is unknown or any requested bars are missing.
            ValueError: The selection mixes exchanges or omits both symbols and universe.
        """
        master = self._catalog
        requested = self._symbols(master, symbols, universe)
        records = [master.get(symbol) for symbol in requested]
        missing_records = [symbol for symbol, record in zip(requested, records, strict=True) if record is None]
        if missing_records:
            raise LookupError(f"unknown instruments: {', '.join(missing_records)}")

        selected = [record for record in records if record is not None]
        exchange_names = {record.exchange.upper() for record in selected}
        if exchange is not None:
            exchange_names = {str(exchange).strip().upper()}
        if len(exchange_names) != 1:
            raise ValueError("historical feed selection must use one exchange")
        exchange_name = next(iter(exchange_names))

        wrong_exchange = [record.symbol for record in selected if record.exchange.upper() != exchange_name]
        if wrong_exchange:
            raise LookupError(
                f"instruments are not listed on {exchange_name}: {', '.join(wrong_exchange)}"
            )

        missing_history = [
            record.symbol for record in selected
            if not record.has_daily or not record.cache_file
        ]
        if missing_history:
            raise LookupError(
                f"daily history is unavailable for: {', '.join(missing_history)}"
            )

        cache_dir = self._daily_cache_root / exchange_name
        cached_frames = self._bar_cache.load(
            cache_dir,
            [Path(record.cache_file).stem for record in selected],
        )
        bar_frames: list[pl.DataFrame] = []
        instruments: dict[str, Instrument] = {}
        missing_files: list[str] = []
        for record in selected:
            cache_file = Path(record.cache_file)
            frame = cached_frames.get(cache_file.stem)
            if frame is None or frame.empty:
                missing_files.append(record.symbol)
                continue
            bars = pl.from_pandas(frame.reset_index(drop=True)).with_columns(
                pl.lit(record.symbol).alias("symbol"),
                pl.col("open").cast(pl.Float64),
                pl.col("high").cast(pl.Float64),
                pl.col("low").cast(pl.Float64),
                pl.col("close").cast(pl.Float64),
                pl.col("volume").cast(pl.Float64),
            )
            bar_frames.append(bars)
            instruments[record.symbol] = self._instrument(record)

        if missing_files:
            raise LookupError(f"daily bars are missing for: {', '.join(missing_files)}")
        if not bar_frames:
            raise LookupError("the selected instruments have no historical bars")

        snapshots = pivot_to_snapshots(
            pl.concat(bar_frames),
            timeframe=Timeframe.DAY_1,
        )
        if not snapshots:
            raise LookupError("the selected instruments have no historical bars")
        return ListFeed(snapshots, instruments)

    @staticmethod
    def _symbols(master, symbols: Sequence[str] | None, universe: str | None) -> list[str]:
        """Resolve the explicit symbol or universe selection."""
        if symbols:
            return list(dict.fromkeys(str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()))
        if universe:
            members = master.universe(universe)
            if not members:
                raise LookupError(f"unknown or empty instrument universe: {universe}")
            return list(members)
        raise ValueError("provide symbols or a named universe for historical data")

    @staticmethod
    def _instrument(record: HistoricalInstrumentRecord) -> Instrument:
        """Build the engine instrument from local contract metadata."""
        return Instrument(
            symbol=record.symbol,
            asset_class=AssetClass(record.asset_class.upper()),
            exchange=record.exchange,
            tick_size=record.tick_size,
            min_quantity=float(record.lot_size),
            quantity_step=float(record.lot_size),
        )
