"""Share in-process caches used by market-data adapters."""

from forward.core.historical_market_data_ports import CachedMarketFramesPort
from forward.data.cache_store import PanelDailyBarCache
from forward.data.cached_market_frames import CachedMarketFramesAdapter

DAILY_BAR_CACHE = PanelDailyBarCache()
CACHED_MARKET_FRAMES = CachedMarketFramesAdapter()


def get_cached_market_frames_port() -> CachedMarketFramesPort:
    """Return the shared adapter for cached daily market frames."""
    return CACHED_MARKET_FRAMES
