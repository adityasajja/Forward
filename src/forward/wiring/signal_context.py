"""Wire signal-context services to shared market and learning data."""

from __future__ import annotations

from forward.appdb.engine import get_app_db
from forward.appdb.signal_context_storage import SqlSignalContextStorage
from forward.config.settings import research_data_root
from forward.data.signal_context_market_data import LocalSignalContextMarketDataAdapter
from forward.signal_context.engine import SignalContextEngine
from forward.signal_context.service import SignalContextService
from forward.wiring.learning_artifacts import LEARNING_ARTIFACTS
from forward.wiring.market_data import DAILY_BAR_CACHE
from forward.wiring.market_intel import get_market_intel_service

_SIGNAL_CONTEXT_SERVICE: SignalContextService | None = None


def get_signal_context_service() -> SignalContextService:
    """Return the signal-context use case with its SQL and market adapters."""
    global _SIGNAL_CONTEXT_SERVICE
    if _SIGNAL_CONTEXT_SERVICE is None:
        _SIGNAL_CONTEXT_SERVICE = SignalContextService(
            storage=SqlSignalContextStorage(get_app_db()),
            engine=SignalContextEngine(),
            market_data=LocalSignalContextMarketDataAdapter(
                data_root=research_data_root(),
                exchange="NSEEQ",
                daily_bars=DAILY_BAR_CACHE,
                learning_artifacts=LEARNING_ARTIFACTS,
            ),
            market_intel=get_market_intel_service(),
        )
    return _SIGNAL_CONTEXT_SERVICE


def reset_signal_context_service() -> None:
    """Clear the signal-context service after database or data-root changes."""
    global _SIGNAL_CONTEXT_SERVICE
    _SIGNAL_CONTEXT_SERVICE = None
