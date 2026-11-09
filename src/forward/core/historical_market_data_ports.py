"""Ports for historical market data."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol


class IntradayHistoryPort(Protocol):
    """Build loaders for recent intraday history."""

    def loaders_for(
        self,
        symbol_exchanges: Mapping[str, str],
        interval: str,
        lookback_days: int,
    ) -> Mapping[str, Callable[[], Any]]:
        """Return a history loader for each requested symbol.

        Args:
            symbol_exchanges: Symbols mapped to their exchange segments.
            interval: Candle interval supported by the data source.
            lookback_days: Calendar days of history to request.

        Returns:
            Loaders keyed by canonical symbol.
        """


class HistoricalInstrumentCatalogPort(Protocol):
    """Resolve instrument records and named universes for historical runs."""

    def get(self, symbol: str) -> HistoricalInstrumentRecord | None:
        """Return metadata for a canonical symbol.

        Args:
            symbol: Canonical or exchange-qualified symbol.

        Returns:
            Its record, or None when the catalog has no match.
        """

    def universe(self, name: str) -> list[str]:
        """Return symbols in a stored universe.

        Args:
            name: Stored universe name.

        Returns:
            Symbols in that universe.
        """


class HistoricalInstrumentRecord(Protocol):
    """Expose metadata needed to build a historical instrument."""

    symbol: str
    exchange: str
    asset_class: str
    tick_size: float
    lot_size: int
    cache_file: str | None
    has_daily: bool


class DailyBarCachePort(Protocol):
    """Read daily bars from the configured local market data cache."""

    def load(self, directory: Path, filenames: Sequence[str]) -> dict[str, Any]:
        """Load frames for the requested parquet files.

        Args:
            directory: Directory containing per-symbol files.
            filenames: File stems requested by the caller.

        Returns:
            Available frames keyed by file stem.
        """

    def load_file(self, path: Path) -> Any | None:
        """Read one symbol's daily frame.

        Args:
            path: Full path to the parquet file.

        Returns:
            The daily frame, or None when the file is unavailable.
        """


class CachedMarketFramesPort(Protocol):
    """Load cached daily frames for application workflows."""

    def symbols(self, exchange: str) -> Sequence[str]:
        """Return symbols with cached daily bars for one exchange."""

    def frames(self, exchange: str, symbols: Sequence[str] | None = None) -> Mapping[str, Any]:
        """Return available daily frames keyed by trading symbol.

        Args:
            exchange: Exchange segment whose history should be loaded.
            symbols: Optional exact symbol list to limit the frames.

        Returns:
            Available daily frames for that exchange.
        """

    def daily_frame(self, symbol: str, exchange: str) -> Any:
        """Return one symbol's daily bars.

        Args:
            symbol: Trading symbol to read.
            exchange: Exchange segment whose cache should be read.

        Returns:
            Daily bars in time order.
        """


class CachedInstrumentHistoryPort(Protocol):
    """Read catalog metadata and cached daily bars for one instrument."""

    def get(self, symbol: str) -> Any | None:
        """Return metadata for a canonical symbol.

        Args:
            symbol: Canonical ticker.

        Returns:
            Instrument metadata, or None when unknown.
        """

    def daily_bars(self, cache_file: str | None) -> Any | None:
        """Read cached bars for an instrument.

        Args:
            cache_file: Relative cache path from the instrument record.

        Returns:
            Daily frame, or None when no cache file is configured.
        """
