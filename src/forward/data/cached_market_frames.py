"""Read cached daily bars for application services."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from forward.core.historical_market_data_ports import CachedMarketFramesPort
from forward.data.history import CACHE_ROOT, load_cached
from forward.data.iifl_history import load_daily_from_cache


class CachedMarketFramesAdapter(CachedMarketFramesPort):
    """Load daily frames from Forward's local market-data cache."""

    def symbols(self, exchange: str) -> list[str]:
        """Return cached symbol names for one exchange.

        Args:
            exchange: Exchange segment whose cache should be listed.

        Returns:
            Sorted trading symbols with a Parquet file in the cache.
        """
        directory = CACHE_ROOT / exchange.upper()
        return sorted(path.stem for path in directory.glob("*.parquet"))

    def frames(
        self, exchange: str, symbols: Sequence[str] | None = None
    ) -> Mapping[str, Any]:
        """Return cached frames for one exchange.

        Args:
            exchange: Exchange segment whose bars should be loaded.
            symbols: Optional exact symbol list to limit the loaded frames.

        Returns:
            Cached daily frames keyed by canonical trading symbol.
        """
        return load_cached(exchange=exchange, symbols=symbols)

    def daily_frame(self, symbol: str, exchange: str) -> Any:
        """Return one symbol's daily bars from its parquet file.

        Args:
            symbol: Trading symbol to read.
            exchange: Exchange segment whose cache should be read.

        Returns:
            Daily bars in time order.
        """
        return load_daily_from_cache(symbol, exchange)
