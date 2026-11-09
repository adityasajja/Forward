"""Market-data adapter for watchlist instrument details and daily bars."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from forward.core.historical_market_data_ports import DailyBarCachePort
from forward.data.instrument_catalog import InstrumentMaster


class InstrumentMasterWatchlistAdapter:
    """Use the instrument master and shared bar cache for watchlist quotes."""

    def __init__(
        self,
        master: InstrumentMaster,
        daily_bars: DailyBarCachePort,
        cache_root: Path,
    ) -> None:
        """Create the adapter with configured catalog and cache dependencies.

        Args:
            master: Instrument catalog used to resolve symbols and metadata.
            daily_bars: Shared adapter for reading cached daily bars.
            cache_root: Root directory for instrument cache files.
        """
        self._master = master
        self._daily_bars = daily_bars
        self._cache_root = cache_root

    def resolve(self, symbol: str) -> str | None:
        """Return a canonical ticker when the catalog contains the symbol.

        Args:
            symbol: User-supplied symbol or exchange-qualified ticker.

        Returns:
            The canonical ticker, or ``None`` when unknown.
        """
        return self._master.resolve(symbol)

    def get(self, symbol: str) -> Any | None:
        """Return catalog metadata for a canonical symbol.

        Args:
            symbol: Canonical market ticker.

        Returns:
            Instrument metadata, or ``None`` when it is not in the catalog.
        """
        return self._master.get(symbol)

    def daily_bars(self, cache_file: str | None) -> Any | None:
        """Read daily bars for an instrument's configured cache file.

        Args:
            cache_file: Cache path relative to the configured data root.

        Returns:
            The cached frame, or ``None`` when no cache file is configured.
        """
        if cache_file is None:
            return None
        return self._daily_bars.load_file(self._cache_root / cache_file)
