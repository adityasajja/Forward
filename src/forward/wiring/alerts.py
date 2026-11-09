"""Wire alert rules and trade-signal services to their adapters."""

from __future__ import annotations

from forward.brokers.iifl.alert_holdings import IiflAlertHoldingsAdapter
from forward.brokers.iifl.alert_quotes import IiflAlertQuoteAdapter
from forward.config.settings import get_settings, research_data_root
from forward.core.alert_ports import (
    AlertHoldingsPort,
    AlertStorePort,
    IntelligentAlertConfigPort,
)
from forward.core.learning_ports import SetupStatisticsPort
from forward.core.market_universe import DEFAULT_SCAN_UNIVERSE
from forward.core.trade_signal_ports import TradeSignalStoragePort
from forward.data.alert_history import CachedAlertHistoryAdapter
from forward.data.history import CACHE_ROOT as DAILY_CACHE_ROOT
from forward.services.alerts import AlertService
from forward.services.signal_queue import SignalQueue
from forward.services.trade_signal_execution import TradeSignalExecutionService
from forward.services.trade_signal_scan import TradeSignalScanService
from forward.services.trade_signal_settings import TradeSignalSettingsService
from forward.wiring.audit import get_audit_log_service
from forward.wiring.broker_access import get_authenticated_broker_client
from forward.wiring.market_data import get_cached_market_frames_port
from forward.wiring.self_learning import get_self_learning_engine

_ALERT_STORE: AlertStorePort | None = None
_ALERT_SERVICE: AlertService | None = None
_ALERT_HOLDINGS: AlertHoldingsPort | None = None
_INTELLIGENT_ALERT_CONFIG: IntelligentAlertConfigPort | None = None
_INTELLIGENT_ALERT_MONITOR = None
_SETUP_STATS_SERVICE: SetupStatisticsPort | None = None
_TRADE_SIGNAL_STORAGE: TradeSignalStoragePort | None = None
_TRADE_SIGNAL_QUEUE: SignalQueue | None = None
_TRADE_SIGNAL_SETTINGS: TradeSignalSettingsService | None = None
_TRADE_SIGNAL_SCAN_SERVICE: TradeSignalScanService | None = None


def get_alert_quote_port(client) -> IiflAlertQuoteAdapter:
    """Build an IIFL quote adapter for the authenticated request client.

    Args:
        client: Authenticated IIFL client used for this alert check.

    Returns:
        The market-quote port backed by that IIFL client.
    """
    return IiflAlertQuoteAdapter(client)


def get_trade_signal_storage() -> TradeSignalStoragePort:
    """Return the JSON adapter for the trade-signal queue and settings."""
    global _TRADE_SIGNAL_STORAGE
    if _TRADE_SIGNAL_STORAGE is None:
        from forward.data.trade_signal_storage import JsonTradeSignalStorageAdapter

        _TRADE_SIGNAL_STORAGE = JsonTradeSignalStorageAdapter(research_data_root())
    return _TRADE_SIGNAL_STORAGE


def get_trade_signal_queue() -> SignalQueue:
    """Return the shared signal queue with configured storage."""
    global _TRADE_SIGNAL_QUEUE
    if _TRADE_SIGNAL_QUEUE is None:
        _TRADE_SIGNAL_QUEUE = SignalQueue(get_trade_signal_storage())
    return _TRADE_SIGNAL_QUEUE


def get_trade_signal_settings() -> TradeSignalSettingsService:
    """Return the settings use case with configured storage."""
    global _TRADE_SIGNAL_SETTINGS
    if _TRADE_SIGNAL_SETTINGS is None:
        _TRADE_SIGNAL_SETTINGS = TradeSignalSettingsService(get_trade_signal_storage())
    return _TRADE_SIGNAL_SETTINGS


def get_trade_signal_scan_service() -> TradeSignalScanService:
    """Return the application workflow for creating trade signals."""
    global _TRADE_SIGNAL_SCAN_SERVICE
    if _TRADE_SIGNAL_SCAN_SERVICE is None:
        from forward.alerts.channels import channels_from_settings
        from forward.alerts.intelligent import evaluate_stock_signals
        from forward.alerts.trade_signal_messages import format_telegram_preview
        from forward.services.trade_signal_factory import build_trade_signal

        _TRADE_SIGNAL_SCAN_SERVICE = TradeSignalScanService(
            alert_config=get_intelligent_alert_config_port(),
            alert_store=get_alert_store(),
            holdings=get_alert_holdings_port(),
            market_frames=get_cached_market_frames_port(),
            queue=get_trade_signal_queue(),
            research_scanner=get_self_learning_engine(),
            settings=get_trade_signal_settings(),
            setup_statistics=get_setup_stats_service(),
            channel_factory=lambda: channels_from_settings(get_settings()),
            default_symbols=list(DEFAULT_SCAN_UNIVERSE),
            evaluate_signals=evaluate_stock_signals,
            build_signal=build_trade_signal,
            format_preview=format_telegram_preview,
        )
    return _TRADE_SIGNAL_SCAN_SERVICE


def build_trade_signal_execution_service() -> TradeSignalExecutionService:
    """Build the order workflow for approving one trade signal."""
    from forward.alerts.channels import channels_from_settings
    from forward.alerts.trade_signal_messages import format_telegram_confirmation

    return TradeSignalExecutionService(
        queue=get_trade_signal_queue(),
        audit=get_audit_log_service(),
        channel_factory=lambda: channels_from_settings(get_settings()),
        format_confirmation=format_telegram_confirmation,
    )


def get_alert_store() -> AlertStorePort:
    """Return the shared JSON store for alert rules and events."""
    global _ALERT_STORE
    if _ALERT_STORE is None:
        from forward.data.alert_store import JsonAlertStoreAdapter

        _ALERT_STORE = JsonAlertStoreAdapter(research_data_root() / "alerts")
    return _ALERT_STORE


def get_alert_service() -> AlertService:
    """Return the alert use case with its configured storage and adapters."""
    global _ALERT_SERVICE
    if _ALERT_SERVICE is None:
        from forward.alerts.channels import channels_from_settings

        _ALERT_SERVICE = AlertService(
            store=get_alert_store(),
            history=get_alert_history_port(),
            channel_factory=lambda: channels_from_settings(get_settings()),
        )
    return _ALERT_SERVICE


def get_alert_holdings_port() -> AlertHoldingsPort:
    """Return the broker adapter that reads held symbols for alert rules."""
    global _ALERT_HOLDINGS
    if _ALERT_HOLDINGS is None:
        _ALERT_HOLDINGS = IiflAlertHoldingsAdapter(get_authenticated_broker_client)
    return _ALERT_HOLDINGS


def get_intelligent_alert_config_port() -> IntelligentAlertConfigPort:
    """Return the JSON adapter for intelligent alert settings."""
    global _INTELLIGENT_ALERT_CONFIG
    if _INTELLIGENT_ALERT_CONFIG is None:
        from forward.data.intelligent_alert_config import JsonIntelligentAlertConfigAdapter

        _INTELLIGENT_ALERT_CONFIG = JsonIntelligentAlertConfigAdapter()
    return _INTELLIGENT_ALERT_CONFIG


def get_setup_stats_service() -> SetupStatisticsPort:
    """Return the setup-statistics use case with its data adapters."""
    global _SETUP_STATS_SERVICE
    if _SETUP_STATS_SERVICE is None:
        from forward.data.setup_stats_cache import PickleSetupStatsCacheAdapter
        from forward.signals_stats import SetupStatsService

        _SETUP_STATS_SERVICE = SetupStatsService(
            market_frames=get_cached_market_frames_port(),
            config=get_intelligent_alert_config_port(),
            cache=PickleSetupStatsCacheAdapter(),
        )
    return _SETUP_STATS_SERVICE


def get_intelligent_alert_monitor():
    """Return the intelligent-alert use case with its configured adapters."""
    global _INTELLIGENT_ALERT_MONITOR
    if _INTELLIGENT_ALERT_MONITOR is None:
        from forward.alerts.channels import channels_from_settings
        from forward.alerts.intelligent import IntelligentMonitorManager
        from forward.services.trade_signal_factory import build_trade_signal

        _INTELLIGENT_ALERT_MONITOR = IntelligentMonitorManager(
            store=get_alert_store(),
            market_frames=get_cached_market_frames_port(),
            holdings=get_alert_holdings_port(),
            channels=channels_from_settings(get_settings()),
            config=get_intelligent_alert_config_port(),
            setup_stats=get_setup_stats_service(),
            signal_queue=get_trade_signal_queue(),
            signal_settings=get_trade_signal_settings(),
            trade_signal_factory=build_trade_signal,
        )
    return _INTELLIGENT_ALERT_MONITOR


def get_alert_history_port() -> CachedAlertHistoryAdapter:
    """Build the history port for the configured daily cache.

    Returns:
        The local daily-bar adapter used by indicator alert rules.
    """
    return CachedAlertHistoryAdapter(DAILY_CACHE_ROOT)
