"""Wire market-intelligence services to local data and broker adapters."""

from __future__ import annotations

from forward.config.settings import get_settings, research_data_root
from forward.core.market_intel_ports import MarketIntelMarketDataPort
from forward.data.market_intel_index_refresh import LocalMarketIntelIndexRefreshAdapter
from forward.data.market_intel_market_data import LocalMarketIntelDataAdapter
from forward.data.market_intel_snapshot import PickleMarketIntelSnapshotAdapter
from forward.market_intel.service import MarketIntelService
from forward.wiring.market_data import DAILY_BAR_CACHE

_MARKET_INTEL_SERVICE: MarketIntelService | None = None
_MARKET_INTEL_DATA: MarketIntelMarketDataPort | None = None


def get_market_intel_service() -> MarketIntelService:
    """Return the shared market-intelligence use case.

    Returns:
        The service connected to local history, broker refresh, and snapshots.
    """
    global _MARKET_INTEL_SERVICE
    if _MARKET_INTEL_SERVICE is None:
        from forward.data.indices import INDIA_VIX, NIFTY_50
        from forward.wiring.broker_access import get_authenticated_broker_client

        data_root = research_data_root()
        _MARKET_INTEL_SERVICE = MarketIntelService(
            market_data=get_market_intel_data_port(),
            index_refresh=LocalMarketIntelIndexRefreshAdapter(
                data_root,
                get_authenticated_broker_client,
                base_url=get_settings().iifl_base_url,
                contract_source="HTTP",
                intervals={NIFTY_50: "1d", INDIA_VIX: "15m"},
            ),
            snapshot_cache=PickleMarketIntelSnapshotAdapter(
                data_root / "panel" / "market_intel.pkl"
            ),
        )
    return _MARKET_INTEL_SERVICE


def get_market_intel_data_port() -> MarketIntelMarketDataPort:
    """Return the cached local market-data adapter for market intelligence."""
    global _MARKET_INTEL_DATA
    if _MARKET_INTEL_DATA is None:
        _MARKET_INTEL_DATA = LocalMarketIntelDataAdapter(
            data_root=research_data_root(),
            exchange="NSEEQ",
            daily_bars=DAILY_BAR_CACHE,
        )
    return _MARKET_INTEL_DATA


def reset_market_intel_service() -> None:
    """Clear market-intelligence providers after settings or data-root changes."""
    global _MARKET_INTEL_SERVICE, _MARKET_INTEL_DATA
    _MARKET_INTEL_SERVICE = None
    _MARKET_INTEL_DATA = None
