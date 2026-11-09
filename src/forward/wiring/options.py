"""Wire options strategy workflows to their storage and broker adapters."""

from __future__ import annotations

from pathlib import Path

from forward.appdb.engine import get_app_db
from forward.appdb.options_storage import SqlOptionsRunStorage
from forward.brokers.iifl.options_forward import IiflOptionsForwardAdapter
from forward.brokers.iifl.options_market import IiflOptionsMarketAdapter
from forward.config.options_forward import load_options_forward_settings
from forward.config.settings import PROJECT_ROOT, get_settings
from forward.data.options_feed import OptionsCsvDatasetAdapter
from forward.live.runner import LiveConfig
from forward.services.options_backtest import OptionsBacktestService
from forward.services.options_forward import OptionsForwardService
from forward.services.options_market import OptionsMarketService
from forward.services.options_runs import OptionsRunService
from forward.services.options_strategies import OptionsStrategyService
from forward.wiring.strategy import get_strategy_service

settings = get_settings()
options_forward_path = Path(settings.options_forward_config_path)
if not options_forward_path.is_absolute():
    options_forward_path = PROJECT_ROOT / options_forward_path
options_forward = load_options_forward_settings(options_forward_path)

_OPTIONS_MARKET_SERVICE = OptionsMarketService(
    IiflOptionsMarketAdapter(
        settings,
        index_exchange=options_forward.index_exchange,
        options_exchange=options_forward.options_exchange,
    )
)
_OPTIONS_DATASET_ADAPTER = OptionsCsvDatasetAdapter(repository_root=PROJECT_ROOT)
_OPTIONS_RUN_STORAGE = SqlOptionsRunStorage(get_app_db())
_OPTIONS_RUN_SERVICE = OptionsRunService(_OPTIONS_RUN_STORAGE, _OPTIONS_DATASET_ADAPTER)
_OPTIONS_FORWARD_SERVICE = OptionsForwardService(
    IiflOptionsForwardAdapter(
        tls_verify=settings.bridge_tls_verify,
        max_quote_age_seconds=settings.options_max_quote_age_seconds,
        bridge_host=settings.bridge_host,
        bridge_port=settings.bridge_port,
        bridge_connect_timeout_seconds=settings.bridge_connect_timeout_seconds,
        bridge_reconnect_min_seconds=settings.bridge_reconnect_min_seconds,
        bridge_reconnect_max_seconds=settings.bridge_reconnect_max_seconds,
        frequency=options_forward.frequency,
    ),
    LiveConfig(freq=options_forward.frequency, **options_forward.runner.model_dump()),
)

# Keep the options backtest tied to the app's shared strategy service.
_OPTIONS_BACKTEST_SERVICE = OptionsBacktestService(
    _OPTIONS_DATASET_ADAPTER,
    _OPTIONS_RUN_SERVICE,
    get_strategy_service(),
    max_curve_points=settings.backtest_max_curve_points,
    max_persisted_trades=int(
        settings.ui_backtest["limits"]["max_persisted_trades"]
    ),
)


def get_options_market_service() -> OptionsMarketService:
    """Return the options-market use case wired to the IIFL adapter."""
    return _OPTIONS_MARKET_SERVICE


def get_options_strategy_service() -> OptionsStrategyService:
    """Return options strategy workflows over the shared strategy service."""
    return OptionsStrategyService(get_strategy_service())


def get_options_backtest_service() -> OptionsBacktestService:
    """Return the options backtest use case wired to the CSV feed adapter."""
    return _OPTIONS_BACKTEST_SERVICE


def get_options_run_service() -> OptionsRunService:
    """Return the F&O persistence use case with its dataset adapter."""
    return _OPTIONS_RUN_SERVICE


def get_options_forward_service() -> OptionsForwardService:
    """Return the options forward-run use case wired for paper execution."""
    return _OPTIONS_FORWARD_SERVICE
