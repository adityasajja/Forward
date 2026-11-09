"""Wire strategy authoring to its database and shared market catalog."""

from __future__ import annotations

from forward.appdb.engine import get_app_db
from forward.appdb.strategy_storage import SqlStrategyAuthoringStorage
from forward.instruments.strategy_universe import ResearchedStrategyUniverse
from forward.services.strategies import StrategyService
from forward.wiring.instrument_catalog import INSTRUMENT_CATALOG
from forward.wiring.market_data import CACHED_MARKET_FRAMES

_STRATEGY_SERVICE = StrategyService(
    SqlStrategyAuthoringStorage(get_app_db()),
    ResearchedStrategyUniverse(CACHED_MARKET_FRAMES, INSTRUMENT_CATALOG),
)


def get_strategy_service() -> StrategyService:
    """Return the shared strategy authoring service."""
    return _STRATEGY_SERVICE
