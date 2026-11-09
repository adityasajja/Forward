"""Build application services with their external adapters."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from forward.appdb.backtest_storage import SqlBacktestStorage
from forward.appdb.champion_storage import SqlChampionStorage
from forward.appdb.daily_learning_storage import SqlDailyLearningStorage
from forward.appdb.engine import get_app_db
from forward.appdb.forward_check_storage import SqlForwardCheckStorage
from forward.appdb.health import AppDatabaseHealthAdapter
from forward.appdb.learning_storage import SqlLearningStorage
from forward.appdb.order_storage import SqlOrderStorage
from forward.appdb.paper_deployment_storage import SqlPaperDeploymentStorage
from forward.appdb.paper_ledger_storage import SqlPaperLedgerStorage
from forward.appdb.portfolio_storage import SqlPortfolioStorage
from forward.appdb.reconciliation_storage import SqlReconciliationStorage
from forward.appdb.risk_state_storage import SqlRiskStateStorage
from forward.appdb.screener_storage import SqlScreenerStorageAdapter
from forward.appdb.strategy_research_storage import SqlStrategyResearchStorage
from forward.appdb.trade_journal_storage import SqlTradeJournalStorage
from forward.appdb.watchlist_storage import SqlWatchlistStorage
from forward.brokers.iifl.auth import SessionStore
from forward.brokers.iifl.chart_market import IiflChartMarketAdapter
from forward.brokers.iifl.contract_cache_status import IiflContractCacheStatusAdapter
from forward.brokers.iifl.live_feed import IiflLiveFeedAdapter
from forward.config.settings import (
    backup_directory,
    get_settings,
    research_data_root,
)
from forward.core.backtest_ports import BacktestFeedPort
from forward.core.backup_ports import BackupPort
from forward.core.broker_catalog_ports import BrokerInstrumentCatalogPort
from forward.core.calendar_ports import MarketCalendarPort
from forward.core.daily_maintenance_ports import DailyMarketMaintenancePort
from forward.core.forward_tracking_ports import (
    ForwardTradeStoragePort,
)
from forward.core.historical_market_data_ports import (
    CachedInstrumentHistoryPort,
    CachedMarketFramesPort,
    IntradayHistoryPort,
)
from forward.core.insights_ports import (
    InsightsHoldingsPort,
)
from forward.core.live_broker_ports import LiveBrokerProviderPort
from forward.core.live_feed_ports import LiveMarketFeedPort
from forward.core.live_market_data_ports import MarketDataPort
from forward.core.market_tracking_ports import TrackRecordMarketDataPort, VwapLiquidityPort
from forward.core.observed_bars_ports import ObservedBarsPort
from forward.core.order_ports import OrderInstrumentLookupPort, OrderStoragePort
from forward.core.paper_ledger_ports import PaperRunnerLedgerPort
from forward.core.paper_models import PaperPriceSourcePort
from forward.core.paper_runner_storage_ports import PaperRunnerStoragePort
from forward.core.pnl_ports import (
    PnlCalendarDataPort,
)
from forward.core.portfolio_ports import (
    PortfolioStoragePort,
)
from forward.core.risk_ports import (
    RiskPortfolioPort,
    RiskStatePort,
)
from forward.core.signal_context_ports import (
    SignalContextEnrichmentPort,
)
from forward.core.system_state_ports import SystemStateStoragePort
from forward.core.trade_journal_ports import (
    TradeJournalStoragePort,
)
from forward.data.backtest_market_data import CachedBacktestMarketDataAdapter
from forward.data.daily_maintenance import LocalDailyMarketMaintenanceAdapter
from forward.data.dashboard_history import CachedDashboardHistoryAdapter
from forward.data.data_status import CachedMarketDataStatusAdapter
from forward.data.forward_tracking import FileForwardTrackingAdapter
from forward.data.historical_feed import CachedHistoricalFeedAdapter
from forward.data.history import CACHE_ROOT as DAILY_CACHE_ROOT
from forward.data.history import DAILY_DIR
from forward.data.history_status import CachedDailyHistoryStatusAdapter
from forward.data.insights_holdings import LocalInsightsHoldingsAdapter
from forward.data.insights_state import JsonInsightsStateAdapter
from forward.data.instrument_catalog import InstrumentMaster
from forward.data.instrument_directory import LocalInstrumentDirectoryAdapter
from forward.data.observed_bars import ObservedBars
from forward.data.pnl_calendar import FilePnlCalendarAdapter
from forward.data.saved_scans import JsonSavedScanStorageAdapter
from forward.data.screener_market import ScreenerMarketDataAdapter
from forward.data.track_record import CachedTrackRecordMarketDataAdapter
from forward.data.vwap_liquidity import NseBhavcopyLiquidityAdapter
from forward.data.watchlist_market import InstrumentMasterWatchlistAdapter
from forward.execution.oms import RiskGate
from forward.infra.backup_adapter import LocalBackupAdapter
from forward.market_calendar import get_market_calendar
from forward.screener.service import ScreenerService
from forward.services.backtests import BacktestService
from forward.services.backup import BackupService
from forward.services.chart_market_data import ChartMarketDataService
from forward.services.daily_learning import DailyLearningCycleService
from forward.services.daily_maintenance import DailyMarketMaintenanceService
from forward.services.dashboard_summary import DashboardSummaryCache, DashboardSummaryService
from forward.services.database_health import DatabaseHealthService
from forward.services.experiment import StrategyExperimentService
from forward.services.forward_check import ForwardCheckService
from forward.services.forward_tracking import ForwardTrackingService
from forward.services.instrument_directory import InstrumentDirectoryService
from forward.services.learning import LearningService
from forward.services.learning import get_learning_service as create_learning_service
from forward.services.market_cache_status import MarketCacheStatusService
from forward.services.market_regime import MarketRegimeService
from forward.services.optimization import OptimizationService
from forward.services.parameter_screen import ParameterScreenService
from forward.services.pnl_calendar import PnlCalendarService
from forward.services.risk import RiskStateService
from forward.services.saved_scans import DEFAULT_SCANS, SavedScanService
from forward.services.screener_watch import ScreenerWatchService
from forward.services.tick_candles import TickCandleService
from forward.services.watchlists import WatchlistService
from forward.strategy.regime_laya import get_regime_classifier
from forward.wiring.audit import get_audit_log_adapter
from forward.wiring.broker_access import (
    get_authenticated_broker_client,
    get_live_broker,
)
from forward.wiring.instrument_catalog import (
    INSTRUMENT_CATALOG,
    get_order_instrument_lookup,
)
from forward.wiring.learning_artifacts import LEARNING_ARTIFACTS
from forward.wiring.market_data import (
    CACHED_MARKET_FRAMES,
    DAILY_BAR_CACHE,
    get_cached_market_frames_port,
)
from forward.wiring.market_intel import (
    get_market_intel_data_port,
    get_market_intel_service,
)
from forward.wiring.paper import (
    get_live_paper_price_source,
    get_paper_ledger,
    get_paper_order_storage,
    get_paper_pnl_storage,
    get_paper_runner_storage,
    get_paper_state_storage,
)
from forward.wiring.signal_context import get_signal_context_service

if TYPE_CHECKING:
    from forward.insights.service import InsightsService
    from forward.services.analytics import AnalyticsService
    from forward.services.attribution import AttributionService
    from forward.services.briefing import BriefingService
    from forward.services.champions import ChampionService
    from forward.services.deployments import DeploymentService
    from forward.services.execution import ExecutionService
    from forward.services.orders import OrderService
    from forward.services.paper_health import PaperHealthService
    from forward.services.portfolio import PortfolioService
    from forward.services.reconcile import ReconciliationService
    from forward.services.reconciliation_notifications import ReconciliationNotifier
    from forward.services.runner import PaperRunner
    from forward.services.track_record import TrackRecordService


_BACKTEST_SERVICE: BacktestService | None = None
_DAILY_LEARNING_SERVICE: DailyLearningCycleService | None = None
_INSIGHTS_HOLDINGS: InsightsHoldingsPort | None = None
_INSIGHTS_SERVICE: InsightsService | None = None
_BRIEFING_SERVICE: BriefingService | None = None
_PAPER_RUNNER: PaperRunner | None = None
_PAPER_HEALTH_SERVICE: Any | None = None
_LIVE_MARKET_FEED: LiveMarketFeedPort | None = None
_DAILY_MAINTENANCE_SERVICE: DailyMarketMaintenanceService | None = None
_BACKUP_SERVICE: BackupService | None = None
_TICK_CANDLE_SERVICE: TickCandleService | None = None


def get_live_market_feed() -> LiveMarketFeedPort:
    """Return the shared live market-feed adapter for this process."""
    global _LIVE_MARKET_FEED
    if _LIVE_MARKET_FEED is None:
        settings = get_settings()
        catalog: BrokerInstrumentCatalogPort = INSTRUMENT_CATALOG
        _LIVE_MARKET_FEED = IiflLiveFeedAdapter(
            session_store=SessionStore(settings.iifl_session_cache),
            instrument_catalog=catalog,
            tls_verify=settings.bridge_tls_verify,
            bridge_host=settings.bridge_host,
            bridge_port=settings.bridge_port,
            bridge_connect_timeout_seconds=settings.bridge_connect_timeout_seconds,
            bridge_reconnect_min_seconds=settings.bridge_reconnect_min_seconds,
            bridge_reconnect_max_seconds=settings.bridge_reconnect_max_seconds,
        )
    return _LIVE_MARKET_FEED


def get_daily_market_maintenance_service() -> DailyMarketMaintenanceService:
    """Return the application service for scheduled daily market-data work."""
    global _DAILY_MAINTENANCE_SERVICE
    if _DAILY_MAINTENANCE_SERVICE is None:
        settings = get_settings()
        maintenance: DailyMarketMaintenancePort = LocalDailyMarketMaintenanceAdapter(
            request_timeout_seconds=settings.market_history_timeout_seconds,
            start_date=settings.market_history_start_date,
            workers=settings.market_history_workers,
        )
        _DAILY_MAINTENANCE_SERVICE = DailyMarketMaintenanceService(maintenance)
    return _DAILY_MAINTENANCE_SERVICE


def get_backup_service() -> BackupService:
    """Return the configured application backup workflow."""
    global _BACKUP_SERVICE
    if _BACKUP_SERVICE is None:
        settings = get_settings()
        backup: BackupPort = LocalBackupAdapter()
        _BACKUP_SERVICE = BackupService(
            backup,
            data_root=research_data_root(),
            destination=backup_directory(),
            keep=settings.backup_keep,
            database=get_app_db(),
        )
    return _BACKUP_SERVICE


def get_tick_candle_service() -> TickCandleService:
    """Return the application service for aggregating live chart candles."""
    global _TICK_CANDLE_SERVICE
    if _TICK_CANDLE_SERVICE is None:
        from forward.analytics.duck_engine import get_duck_engine

        _TICK_CANDLE_SERVICE = TickCandleService(get_duck_engine)
    return _TICK_CANDLE_SERVICE


def build_observed_bars_port() -> ObservedBarsPort:
    """Build the collector that turns live quotes into session bars."""
    return ObservedBars()


_INSTRUMENT_MASTER = InstrumentMaster(cache_root=research_data_root())
_SCREENER_MARKET_DATA = ScreenerMarketDataAdapter(
    _INSTRUMENT_MASTER,
    get_settings().screener_frame_cache_seconds,
)
_INSTRUMENT_DIRECTORY_SERVICE = InstrumentDirectoryService(
    LocalInstrumentDirectoryAdapter(_INSTRUMENT_MASTER)
)
_MARKET_REGIME_SERVICE = MarketRegimeService(get_regime_classifier())
_DASHBOARD_SUMMARY_SERVICE = DashboardSummaryService(
    CachedDashboardHistoryAdapter(), DashboardSummaryCache()
)
_SAVED_SCAN_SERVICE = SavedScanService(
    JsonSavedScanStorageAdapter(Path(get_settings().saved_scans_path)),
    DEFAULT_SCANS,
)
_DATABASE_HEALTH_SERVICE = DatabaseHealthService(
    AppDatabaseHealthAdapter(get_app_db(), get_settings().database_health_cache_seconds)
)
_MARKET_CACHE_STATUS_SERVICE = MarketCacheStatusService(
    CachedDailyHistoryStatusAdapter(DAILY_CACHE_ROOT), IiflContractCacheStatusAdapter()
)
_VWAP_LIQUIDITY = NseBhavcopyLiquidityAdapter(research_data_root())
_HISTORICAL_FEED = CachedHistoricalFeedAdapter(
    catalog=_INSTRUMENT_MASTER,
    bar_cache=DAILY_BAR_CACHE,
    daily_cache_root=_INSTRUMENT_MASTER.cache_root / DAILY_DIR,
)
_STRATEGY_EXPERIMENT_SERVICE = StrategyExperimentService(
    SqlStrategyResearchStorage(get_app_db()),
    historical_feed=_HISTORICAL_FEED,
    curve_max_points=get_settings().experiment_curve_max_points,
    result_decimals=get_settings().experiment_result_decimals,
)
_PORTFOLIO_STORAGE = SqlPortfolioStorage(get_app_db())
_LEARNING_STORAGE = SqlLearningStorage(get_app_db())
_DAILY_LEARNING_STORAGE = SqlDailyLearningStorage(get_app_db())
_LEARNING_SERVICE = create_learning_service(
    _LEARNING_STORAGE,
    _PORTFOLIO_STORAGE,
    _DAILY_LEARNING_STORAGE,
    CACHED_MARKET_FRAMES,
    LEARNING_ARTIFACTS,
)
_OPTIMIZATION_SERVICE = OptimizationService(
    SqlStrategyResearchStorage(get_app_db()),
    learning_service=_LEARNING_SERVICE,
    historical_feed=_HISTORICAL_FEED,
)
_RISK_STATE_SERVICE = RiskStateService(SqlRiskStateStorage(get_app_db()), get_settings())
_FORWARD_TRADE_STORAGE = SqlForwardCheckStorage(get_app_db())
_FORWARD_CHECK_SERVICE = ForwardCheckService(_FORWARD_TRADE_STORAGE)
_FORWARD_TRACKING_SERVICE = ForwardTrackingService(FileForwardTrackingAdapter(research_data_root()))
_CHAMPION_STORAGE = SqlChampionStorage(get_app_db())


def get_backtest_service() -> BacktestService:
    """Return the backtest use case wired to its SQL storage adapter.

    Returns:
        The shared backtest service.
    """
    global _BACKTEST_SERVICE
    if _BACKTEST_SERVICE is None:
        _BACKTEST_SERVICE = BacktestService(
            SqlBacktestStorage(get_app_db()),
            CachedBacktestMarketDataAdapter(
                catalog=_INSTRUMENT_MASTER,
                daily_cache=DAILY_BAR_CACHE,
                cache_root=_INSTRUMENT_MASTER.cache_root,
            ),
            signal_context=get_signal_context_service(),
            screener_market_data=_SCREENER_MARKET_DATA,
            workers=get_settings().backtest_worker_count,
            max_persisted_trades=int(
                get_settings().ui_backtest["limits"]["max_persisted_trades"]
            ),
            max_curve_points=get_settings().backtest_max_curve_points,
            shutdown_timeout_seconds=get_settings().backtest_shutdown_timeout_seconds,
        )
    return _BACKTEST_SERVICE


def build_synthetic_backtest_feed(
    *, symbols: tuple[str, ...], start: datetime, end: datetime, futures: bool
) -> BacktestFeedPort:
    """Build a synthetic feed for an explicit preview request.

    Args:
        symbols: Symbols included in the generated feed.
        start: First timestamp in the generated feed.
        end: Last timestamp in the generated feed.
        futures: Whether generated instruments use futures contracts.

    Returns:
        Synthetic market snapshots behind the backtest feed port.
    """
    from forward.data.synthetic import SyntheticConfig, SyntheticFeed

    return SyntheticFeed(SyntheticConfig(symbols=symbols, start=start, end=end), futures=futures)


def reset_backtest_service() -> None:
    """Shut down the cached backtest worker before clearing its adapters."""
    global _BACKTEST_SERVICE
    if _BACKTEST_SERVICE is not None:
        _BACKTEST_SERVICE.shutdown()
        _BACKTEST_SERVICE = None


def get_forward_check_service() -> ForwardCheckService:
    """Return the forward-versus-backtest comparison use case.

    Returns:
        The shared comparison service.
    """
    return _FORWARD_CHECK_SERVICE


def get_forward_trade_storage() -> ForwardTradeStoragePort:
    """Return the shared storage adapter for forward journal evidence.

    Returns:
        The SQL adapter used by readiness gates and comparison services.
    """
    return _FORWARD_TRADE_STORAGE


def reset_insights_service() -> None:
    """Clear insights providers that hold the current data root."""
    global _INSIGHTS_HOLDINGS, _INSIGHTS_SERVICE
    _INSIGHTS_HOLDINGS = None
    _INSIGHTS_SERVICE = None


def get_insights_service() -> InsightsService:
    """Return the daily insights use case with the shared market summary port."""
    global _INSIGHTS_SERVICE
    if _INSIGHTS_SERVICE is None:
        from forward.insights.service import InsightsService, InsightsSettings
        from forward.insights.stall import StallConfig
        from forward.insights.watch import WatchConfig

        data_root = research_data_root()
        settings = get_settings()
        _INSIGHTS_SERVICE = InsightsService(
            holdings=get_insights_holdings_port(),
            storage=JsonInsightsStateAdapter(data_root),
            watchlists=get_watchlist_service(),
            market_intel=get_market_intel_service(),
            market_data=get_market_intel_data_port(),
            stall_config=StallConfig(
                **{
                    field_name: getattr(settings, f"stall_{field_name}")
                    for field_name in StallConfig.model_fields
                }
            ),
            watch_config=WatchConfig(
                range_quiet_ratio=settings.watch_range_quiet_ratio,
                protect_at_gain_pct=settings.watch_protect_at_gain_pct,
                trail_below_high_pct=settings.watch_trail_below_high_pct,
                lagging_rs_pct=settings.watch_lagging_rs_pct,
            ),
            default_settings=InsightsSettings(**settings.insights_default_settings),
            stale_after_days=settings.insights_stale_after_days,
            cache_ttl_seconds=settings.insights_cache_ttl_seconds,
        )
    return _INSIGHTS_SERVICE


def get_insights_holdings_port() -> InsightsHoldingsPort:
    """Return the configured local adapter for insight holdings."""
    global _INSIGHTS_HOLDINGS
    if _INSIGHTS_HOLDINGS is None:
        data_root = research_data_root()
        _INSIGHTS_HOLDINGS = LocalInsightsHoldingsAdapter(data_root)
    return _INSIGHTS_HOLDINGS


def get_data_status() -> dict[str, Any]:
    """Build data provenance from the application's configured data root.

    Returns:
        Freshness and provenance for the configured market data.
    """
    from forward.services.data_status import build_status

    return build_status(
        CachedMarketDataStatusAdapter(research_data_root()),
        calendar=get_market_calendar(),
    )


def get_deployment_service() -> DeploymentService:
    """Build a deployment use case with one shared database adapter.

    Returns:
        The deployment use case wired to the application database.
    """
    from forward.services.deployments import DeploymentService

    db = get_app_db()
    return DeploymentService(
        storage=SqlPaperDeploymentStorage(db),
        ledger=get_paper_ledger(),
        runner_interval_seconds=get_settings().runner_interval_seconds,
        runner_lookback_days=get_settings().runner_lookback_days,
        forward_trade_storage=get_forward_trade_storage(),
        portfolio_storage=get_portfolio_storage(),
    )


def get_champion_service() -> ChampionService:
    """Build the champion use case with its shared application services.

    Returns:
        The champion use case with its data and deployment services.
    """
    from forward.services.champions import ChampionService

    return ChampionService(
        storage=_CHAMPION_STORAGE,
        learning=_LEARNING_SERVICE,
        contexts=get_signal_context_service(),
        deployments=get_deployment_service(),
    )


def get_track_record_service() -> TrackRecordService:
    """Build the paper track-record use case with shared storage adapters.

    Returns:
        The track-record use case and its paper ledger.
    """
    from forward.services.track_record import TrackRecordService

    return TrackRecordService(
        storage=_PORTFOLIO_STORAGE,
        ledger=get_paper_ledger(),
        market_data=get_track_record_market_data(),
        calendar=get_market_calendar(),
    )


def get_track_record_market_data() -> TrackRecordMarketDataPort:
    """Build the local history adapter used by paper track records.

    Returns:
        Adapter for held-instrument closes and benchmark provenance.
    """
    return CachedTrackRecordMarketDataAdapter(
        get_cached_instrument_history_port(), research_data_root()
    )


def get_attribution_service() -> AttributionService:
    """Build the post-trade attribution use case with the shared database.

    Returns:
        The attribution use case wired to the application database.
    """
    return _attribution_service(get_app_db())


@lru_cache(maxsize=1)
def get_analytics_service() -> AnalyticsService:
    """Return analytics queries wired to the shared attribution service.

    Returns:
        The analytics use case for the configured application database.
    """
    from forward.config.settings import get_settings
    from forward.services.analytics import AnalyticsService

    settings = get_settings()
    return AnalyticsService(
        get_attribution_service(),
        max_aggregate_rows=settings.analytics_max_aggregate_rows,
        min_sample=settings.analytics_min_sample,
        slippage_high_bps=settings.attribution_slippage_high_bps,
    )


@lru_cache(maxsize=1)
def _attribution_service(database: Any) -> AttributionService:
    """Build attribution with its storage and ledger adapters."""
    from forward.appdb.attribution_storage import SqlAttributionStorage
    from forward.services.attribution import AttributionService
    from forward.services.journal import TradeJournalService
    from forward.services.paper_ledger import PaperLedger

    ledger = PaperLedger(
        storage=SqlPaperLedgerStorage(database),
        price_source=get_live_paper_price_source(),
    )
    return AttributionService(
        storage=SqlAttributionStorage(database),
        journal_service=TradeJournalService(
            storage=SqlTradeJournalStorage(database),
            ledger=ledger,
        ),
        ledger=ledger,
        market_history=CACHED_MARKET_FRAMES,
        thresholds={
            "slippage_high_bps": get_settings().attribution_slippage_high_bps,
            "slippage_low_bps": get_settings().attribution_slippage_low_bps,
            "favorable_mfe_r": get_settings().attribution_favorable_mfe_r,
            "high_mae_r": get_settings().attribution_high_mae_r,
            "risk_wide_factor": get_settings().attribution_risk_wide_factor,
            "risk_tight_factor": get_settings().attribution_risk_tight_factor,
            "capture_early_pct": get_settings().attribution_capture_early_pct,
            "giveback_late_pct": get_settings().attribution_giveback_late_pct,
            "giveback_min_peak_r": get_settings().attribution_giveback_min_peak_r,
            "sizing_oversize_pct": get_settings().attribution_sizing_oversize_pct,
            "sizing_undersize_pct": get_settings().attribution_sizing_undersize_pct,
            "context_positive_score": get_settings().attribution_context_positive_score,
            "context_negative_score": get_settings().attribution_context_negative_score,
        },
    )


def get_trade_journal_storage() -> SqlTradeJournalStorage:
    """Return the journal adapter for the configured application database.

    Returns:
        The SQL adapter for journal reconciliation.
    """
    return SqlTradeJournalStorage(get_app_db())


def get_portfolio_service() -> PortfolioService:
    """Build the portfolio use case with its SQL storage adapter.

    Returns:
        The portfolio use case wired to its storage adapter.
    """
    from forward.services.portfolio import PortfolioService

    return PortfolioService(storage=get_portfolio_storage())


def get_portfolio_storage() -> PortfolioStoragePort:
    """Return the storage adapter used by portfolio decisions and reports.

    Returns:
        The shared SQL adapter for portfolio records.
    """
    return _PORTFOLIO_STORAGE


def get_learning_service() -> LearningService:
    """Return the learning use case with its storage adapters.

    Returns:
        The process-wide learning service.
    """
    return _LEARNING_SERVICE


def get_reconciliation_service() -> ReconciliationService:
    """Build the reconciliation use case with the configured application database.

    Returns:
        The reconciliation use case wired to the application database.
    """
    from forward.services.reconcile import ReconciliationService

    return ReconciliationService(
        storage=SqlReconciliationStorage(get_app_db()),
        ledger=get_paper_ledger(),
        quantity_tolerance=get_settings().reconcile_quantity_tolerance,
        cash_tolerance=get_settings().reconcile_cash_tolerance,
    )


def get_reconciliation_notifier() -> ReconciliationNotifier:
    """Build the configured notification callback for reconciliation reports."""
    from forward.services.reconciliation_notifications import ReconciliationNotifier

    def channel_factory() -> Any:
        from forward.alerts.channels import channels_from_settings

        return channels_from_settings(get_settings())

    return ReconciliationNotifier(channel_factory)


def get_strategy_experiment_service() -> StrategyExperimentService:
    """Return the experiment use case with the configured history adapter."""
    return _STRATEGY_EXPERIMENT_SERVICE


def get_optimization_service() -> OptimizationService:
    """Return the optimization use case with the configured history adapter."""
    return _OPTIMIZATION_SERVICE


def get_forward_tracking_service() -> ForwardTrackingService:
    """Return the dashboard tracking queries with their file adapter."""
    return _FORWARD_TRACKING_SERVICE


def get_database_health_service() -> DatabaseHealthService:
    """Return the configured database health use case."""
    return _DATABASE_HEALTH_SERVICE


def get_market_cache_status_service() -> MarketCacheStatusService:
    """Return the configured local market cache status use case."""
    return _MARKET_CACHE_STATUS_SERVICE


def get_instrument_directory_service() -> InstrumentDirectoryService:
    """Return the configured local instrument directory use case."""
    return _INSTRUMENT_DIRECTORY_SERVICE


def get_market_regime_service() -> MarketRegimeService:
    """Return the configured market-regime evaluation use case."""
    return _MARKET_REGIME_SERVICE


def get_dashboard_summary_service() -> DashboardSummaryService:
    """Return the dashboard metrics service with its history adapter."""
    return _DASHBOARD_SUMMARY_SERVICE


@lru_cache(maxsize=1)
def get_parameter_screen_service() -> ParameterScreenService:
    """Return the shared market-data screen service."""
    from forward.services.parameter_screen import ParameterScreenConfig

    values = get_settings().parameter_screen_config
    return ParameterScreenService(
        get_cached_market_frames_port(),
        ParameterScreenConfig(
            exchange=str(values["exchange"]),
            minimum_bars=int(values["minimum_bars"]),
            fast_periods=tuple(int(value) for value in values["fast_periods"]),
            slow_periods=tuple(int(value) for value in values["slow_periods"]),
            chunk_size=int(values["chunk_size"]),
            candidate_limit=int(values["candidate_limit"]),
            screen_cost_bps=float(values["screen_cost_bps"]),
        ),
    )


def get_vwap_liquidity_port() -> VwapLiquidityPort:
    """Return the adapter for prior-session NSE bhavcopy liquidity.

    Returns:
        Shared local liquidity adapter used by the VWAP paper strategy.
    """
    return _VWAP_LIQUIDITY


def get_screener_service() -> ScreenerService:
    """Build a screener use case with its SQL and market-data adapters."""
    from forward.screener.service import ScreenerConfig

    config = get_settings().ui_screener
    return ScreenerService(
        SqlScreenerStorageAdapter(get_app_db()),
        _SCREENER_MARKET_DATA,
        ScreenerConfig(
            max_leaves=int(config["max_leaves"]),
            max_group_count=int(config["max_group_count"]),
            min_bars=int(config["min_bars"]),
            worker_count=int(config["worker_count"]),
            default_sort=str(config["default_sort"]),
            default_universe=str(config["default_universe"]),
            default_exchange=str(config["default_exchange"]),
            default_result_limit=int(config["default_result_limit"]),
            default_sort_descending=bool(config["default_sort_descending"]),
        ),
    )


def get_screener_watch_service() -> ScreenerWatchService:
    """Build the saved-screen watch workflow with its Telegram adapter."""
    from forward.alerts.channels import TelegramChannel
    from forward.services.screener_watch import ScreenerWatchConfig

    settings = get_settings()
    return ScreenerWatchService(
        get_screener_service(),
        TelegramChannel(settings.telegram_bot_token, settings.telegram_chat_id),
        ScreenerWatchConfig(
            minimum_interval_minutes=settings.screener_min_interval_minutes,
            maximum_interval_minutes=settings.screener_request_interval_max_minutes,
            notification_line_limit=settings.screener_max_lines,
        ),
    )


def get_saved_scan_service() -> SavedScanService:
    """Return the use case for saved custom scans."""
    return _SAVED_SCAN_SERVICE


def build_chart_market_data_service(client: Any) -> ChartMarketDataService:
    """Create chart market data use cases for one authenticated broker client.

    Args:
        client: Authenticated IIFL client for the current request.

    Returns:
        Chart market data service using the shared contract catalog.
    """
    return ChartMarketDataService(INSTRUMENT_CATALOG, IiflChartMarketAdapter(client))


def get_cached_instrument_history_port() -> CachedInstrumentHistoryPort:
    """Return instrument metadata and daily bars through the shared cache.

    Returns:
        Port for listed instrument records and their cached daily frames.
    """
    master = _INSTRUMENT_MASTER
    return InstrumentMasterWatchlistAdapter(
        master=master,
        daily_bars=DAILY_BAR_CACHE,
        cache_root=master.cache_root,
    )


def get_daily_learning_service() -> DailyLearningCycleService:
    """Return the daily learning use case with configured optimization."""
    global _DAILY_LEARNING_SERVICE
    if _DAILY_LEARNING_SERVICE is None:
        _DAILY_LEARNING_SERVICE = DailyLearningCycleService(
            storage=_DAILY_LEARNING_STORAGE,
            learning_service=_LEARNING_SERVICE,
            optimization_service=_OPTIMIZATION_SERVICE,
            context_service=get_signal_context_service(),
        )
    return _DAILY_LEARNING_SERVICE


def reset_daily_learning_service() -> None:
    """Clear the cached daily learning use case."""
    global _DAILY_LEARNING_SERVICE
    _DAILY_LEARNING_SERVICE = None


def get_risk_state_service() -> RiskStateService:
    """Return the risk use case with its durable SQL adapter."""
    return _RISK_STATE_SERVICE


def get_paper_health_service(market_data: MarketDataPort) -> PaperHealthService:
    """Return the paper-health use case with its configured ports.

    Args:
        market_data: Shared live-market stream used to check prices and signals.

    Returns:
        The process-wide paper-health service.
    """
    global _PAPER_HEALTH_SERVICE
    if _PAPER_HEALTH_SERVICE is None:
        from forward.services.paper_health import PaperHealthService

        _PAPER_HEALTH_SERVICE = PaperHealthService(
            market_data=market_data,
            history=get_cached_market_frames_port(),
            storage=get_paper_runner_storage(),
            runner=_PAPER_RUNNER,
            calendar=get_market_calendar(),
        )
    return _PAPER_HEALTH_SERVICE


def create_paper_runner(
    *,
    state_storage: SystemStateStoragePort,
    runner_storage: PaperRunnerStoragePort,
    order_storage: OrderStoragePort,
    risk_state: RiskStatePort,
    portfolio_storage: PortfolioStoragePort,
    sector_table: Mapping[str, str],
    instrument_lookup: OrderInstrumentLookupPort,
    journal_storage: TradeJournalStoragePort,
    ledger: PaperRunnerLedgerPort,
    broker_provider: LiveBrokerProviderPort,
    daily_history: CachedMarketFramesPort,
    instrument_history: CachedInstrumentHistoryPort,
    vwap_liquidity: VwapLiquidityPort,
    signal_context: SignalContextEnrichmentPort,
    calendar: MarketCalendarPort,
    price_source: PaperPriceSourcePort,
    market_data: MarketDataPort | None = None,
    intraday_history: IntradayHistoryPort | None = None,
) -> PaperRunner:
    """Build and store the process paper runner from explicit dependencies.

    Args:
        state_storage: Durable runner state adapter.
        runner_storage: Deployment and strategy adapter.
        order_storage: Order lifecycle adapter.
        risk_state: Current risk policy and state.
        portfolio_storage: Portfolio data adapter.
        sector_table: Canonical symbol-to-industry catalog.
        instrument_lookup: Lookup for exact listed order instruments.
        journal_storage: Trade journal adapter.
        ledger: Paper account ledger.
        broker_provider: Authenticated live broker factory.
        daily_history: Cached daily bars.
        instrument_history: Instrument catalog and daily bars.
        vwap_liquidity: Prior-session liquidity data.
        signal_context: Signal annotation service.
        calendar: Exchange session calendar for strategy evaluation.
        price_source: Live quote source for paper execution.
        market_data: Live market feed for the running strategies.
        intraday_history: Exact intraday contract history.

    Returns:
        The configured process paper runner.

    Raises:
        RuntimeError: If the process runner has already been created.
    """
    global _PAPER_RUNNER, _PAPER_HEALTH_SERVICE
    if _PAPER_RUNNER is not None:
        raise RuntimeError("The paper runner has already been created")

    from forward.wiring.paper_runner import build_paper_runner

    runner = build_paper_runner(
        state_storage=state_storage,
        runner_storage=runner_storage,
        order_storage=order_storage,
        risk_state=risk_state,
        portfolio_storage=portfolio_storage,
        sector_table=sector_table,
        instrument_lookup=instrument_lookup,
        journal_storage=journal_storage,
        ledger=ledger,
        broker_provider=broker_provider,
        daily_history=daily_history,
        instrument_history=instrument_history,
        vwap_liquidity=vwap_liquidity,
        signal_context=signal_context,
        calendar=calendar,
        price_source=price_source,
        interval_seconds=get_settings().runner_interval_seconds,
        lookback_days=get_settings().runner_lookback_days,
        minimum_sleep_seconds=get_settings().runner_min_sleep_seconds,
        market_wait_seconds=get_settings().runner_market_wait_seconds,
        symbol_retry_seconds=get_settings().deployment_symbol_retry_seconds,
        max_quote_age_seconds=get_settings().market_quote_max_age_seconds,
        average_volume_days=get_settings().paper_average_volume_days,
        market_data=market_data,
        intraday_history=intraday_history,
    )
    _PAPER_RUNNER = runner
    _PAPER_HEALTH_SERVICE = None
    return runner


def start_paper_runner(*, market_data: MarketDataPort) -> PaperRunner:
    """Build and start the shared paper runner with application adapters.

    Args:
        market_data: Live feed used by paper deployments.

    Returns:
        The started process runner.
    """
    from forward.brokers.iifl.intraday_history import IiflIntradayHistoryAdapter
    from forward.services.paper_prices import live_tick_source

    runner = create_paper_runner(
        state_storage=get_paper_state_storage(),
        runner_storage=get_paper_runner_storage(),
        order_storage=get_paper_order_storage(),
        risk_state=get_risk_state_service(),
        portfolio_storage=get_portfolio_storage(),
        sector_table=LEARNING_ARTIFACTS.sector_table(),
        instrument_lookup=get_order_instrument_lookup(),
        journal_storage=get_trade_journal_storage(),
        ledger=get_paper_ledger(),
        broker_provider=get_live_broker,
        daily_history=get_cached_market_frames_port(),
        instrument_history=get_cached_instrument_history_port(),
        vwap_liquidity=get_vwap_liquidity_port(),
        signal_context=get_signal_context_service(),
        calendar=get_market_calendar(),
        price_source=live_tick_source(market_data),
        market_data=market_data,
        intraday_history=IiflIntradayHistoryAdapter(get_authenticated_broker_client),
    )
    runner.start()
    return runner


def get_paper_runner() -> PaperRunner:
    """Return the process runner after application startup configures it.

    Returns:
        The configured paper runner.

    Raises:
        RuntimeError: If startup has not created the runner.
    """
    if _PAPER_RUNNER is None:
        raise RuntimeError("The paper runner has not been created")
    return _PAPER_RUNNER


def reset_paper_runner() -> None:
    """Clear the process runner for an isolated application setup."""
    global _PAPER_RUNNER, _PAPER_HEALTH_SERVICE
    _PAPER_RUNNER = None
    _PAPER_HEALTH_SERVICE = None


def get_pnl_calendar_data() -> PnlCalendarDataPort:
    """Return the local data adapter for the P&L calendar."""
    return FilePnlCalendarAdapter(research_data_root())


def build_pnl_calendar_service() -> PnlCalendarService:
    """Build the P&L calendar use case with its data adapters."""
    return PnlCalendarService(
        data=get_pnl_calendar_data(),
        paper_storage=get_paper_pnl_storage(),
        holdings=get_insights_holdings_port(),
    )


def get_deployment_monitor():
    """Build deployment monitoring with its storage and ledger adapters."""
    from forward.services.monitoring import DeploymentMonitor

    return DeploymentMonitor(
        portfolio_storage=_PORTFOLIO_STORAGE,
        order_storage=SqlOrderStorage(get_app_db()),
        ledger=get_paper_ledger(),
        risk_state=get_risk_state_service(),
        runner=get_paper_runner(),
        calendar=get_market_calendar(),
    )


def build_order_service(
    *, portfolio: RiskPortfolioPort, portfolio_gate: RiskGate
) -> OrderService:
    """Build an order service with an explicit account book and risk use case.

    Args:
        portfolio: Current account portfolio used by position-aware risk checks.
        portfolio_gate: Required account and fee completeness gate.

    Returns:
        The order service wired to the configured database and risk state.
    """
    from forward.services.orders import get_order_service

    return get_order_service(
        storage=get_paper_order_storage(),
        risk=get_risk_state_service(),
        instruments=get_order_instrument_lookup(),
        portfolio=portfolio,
        portfolio_gate=portfolio_gate,
    )


def build_account_order_service(
    *, user_id: str, mode: str, deployment_id: str | None = None
) -> OrderService:
    """Build an order service with the account book selected by execution mode.

    Args:
        user_id: Account that owns the order.
        mode: Exact execution mode, ``PAPER`` or ``LIVE``.
        deployment_id: Optional paper deployment whose book should be checked.

    Returns:
        The order service wired to the selected account portfolio.

    Raises:
        ValueError: If the execution mode is not supported.
        BrokerUnavailable: If live account access cannot be established.
    """
    if mode == "PAPER":
        portfolio = get_paper_ledger().portfolio(user_id, deployment_id=deployment_id, mode="PAPER")
    elif mode == "LIVE":
        from forward.brokers.portfolio import BrokerPortfolio

        portfolio = BrokerPortfolio(get_live_broker())
    else:
        raise ValueError(f"unsupported order mode: {mode!r}")
    from forward.services.portfolio_risk import portfolio_gate_for

    portfolio_gate = portfolio_gate_for(
        user_id,
        storage=get_portfolio_storage(),
        sector_table=LEARNING_ARTIFACTS.sector_table(),
        order_portfolio=portfolio,
    )
    return build_order_service(portfolio=portfolio, portfolio_gate=portfolio_gate)


def build_paper_execution_service(user_id: str, deployment_id: str) -> ExecutionService:
    """Build the paper order path for one owned deployment.

    Args:
        user_id: Account that owns the deployment.
        deployment_id: Deployment whose paper positions limit new orders.

    Returns:
        The shared execution use case wired to the paper venue.
    """
    from forward.brokers.paper_venue import paper_venue
    from forward.services.execution import ExecutionService
    from forward.services.paper_prices import cached_adv_source
    from forward.services.portfolio_risk import portfolio_gate_for

    ledger = get_paper_ledger()
    portfolio = ledger.portfolio(user_id, deployment_id=deployment_id)
    portfolio_gate = portfolio_gate_for(
        user_id,
        storage=get_portfolio_storage(),
        sector_table=LEARNING_ARTIFACTS.sector_table(),
    )
    return ExecutionService(
        orders=build_order_service(portfolio=portfolio, portfolio_gate=portfolio_gate),
        venue=paper_venue(
            get_live_paper_price_source(),
            instruments=get_order_instrument_lookup(),
            adv=cached_adv_source(
                get_cached_instrument_history_port(),
                days=get_settings().paper_average_volume_days,
            ),
            max_tick_age_seconds=get_settings().market_quote_max_age_seconds,
        ),
    )


def build_live_execution_service(broker: Any, *, user_id: str) -> ExecutionService:
    """Build the live order use case with broker-backed risk and execution.

    Args:
        broker: Authenticated broker used for positions and order submission.

    Returns:
        The execution service wired to the live broker venue.
    """
    from forward.brokers.portfolio import BrokerPortfolio
    from forward.brokers.venue import iifl_venue
    from forward.services.portfolio_risk import portfolio_gate_for

    portfolio = BrokerPortfolio(broker)
    portfolio_gate = portfolio_gate_for(
        user_id,
        storage=get_portfolio_storage(),
        sector_table=LEARNING_ARTIFACTS.sector_table(),
        order_portfolio=portfolio,
    )
    return ExecutionService(
        orders=build_order_service(portfolio=portfolio, portfolio_gate=portfolio_gate),
        venue=iifl_venue(broker, get_order_instrument_lookup()),
    )


def get_watchlist_service() -> WatchlistService:
    """Return the watchlist use case with its SQL and market adapters."""
    return WatchlistService(
        storage=SqlWatchlistStorage(get_app_db(), get_audit_log_adapter()),
        market=get_cached_instrument_history_port(),
    )


def get_briefing_service() -> BriefingService:
    """Return the briefing use case with configured storage and history."""
    global _BRIEFING_SERVICE
    if _BRIEFING_SERVICE is None:
        from forward.alerts.channels import channels_from_settings
        from forward.data.briefing_storage import JsonBriefingStorageAdapter
        from forward.services.briefing import BriefingService

        _BRIEFING_SERVICE = BriefingService(
            storage=JsonBriefingStorageAdapter(research_data_root()),
            market_frames=get_cached_market_frames_port(),
            channels=lambda: channels_from_settings(get_settings()),
        )
    return _BRIEFING_SERVICE
