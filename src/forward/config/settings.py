"""Central configuration.

All values are overridable via environment variables or a local `.env` file.
Nothing here should require code changes to move between dev, paper, and live.
"""

from __future__ import annotations

from datetime import date, time
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
        case_sensitive=False,
    )

    env: str = Field(pattern="^(dev|paper|live)$")
    log_level: str
    scanner_start_date: str
    market_scan_cache_seconds: float = Field(gt=0)
    market_history_timeout_seconds: float = Field(gt=0)
    market_history_start_date: date
    market_history_workers: int = Field(gt=0)
    saved_scans_path: str
    research_data_directory: str

    # ---------------- IIFL Capital ----------------
    iifl_app_key: str
    iifl_app_secret: str
    iifl_redirect_url: str | None
    iifl_base_url: str
    iifl_timeout: float
    iifl_session_cache: str
    iifl_contract_cache_ttl_seconds: float = Field(gt=0)
    iifl_default_product: str
    iifl_api_order_source: str
    #: Pin API egress to IPv4. IIFL whitelists an IPv4 address; on a dual-stack
    #: connection requests otherwise leave over IPv6 and are rejected with
    #: EC500 "IP address not authorized for trading".
    iifl_force_ipv4: bool
    #: Market-protection band applied to MARKET orders. SEBI requires a
    #: non-zero value on API market orders since 2026-04-01.
    iifl_market_protection_percent: float

    # ---------------- Live-order safety ----------------
    #: A ceiling on the value of ONE live order, applied when the operator has not set their own
    #: ``max_order_notional``. With no limit configured a live order of any size used to go through,
    #: which makes a mistyped quantity unbounded. This is a floor of protection, not a recommendation:
    #: set a real limit under Risk & limits and it replaces this. ``0`` or less turns it off.
    live_default_max_order_notional: float
    #: Refuse live orders when NSE is closed (weekends, holidays, outside 09:00-15:30 IST). Turn off
    #: only if you deliberately place after-market orders.
    enforce_market_hours: bool
    market_open: time
    market_close: time
    order_window_open: time
    market_calendar_path: str

    # Market data bridge (MQTT)
    bridge_host: str
    bridge_port: int = Field(ge=1, le=65535)
    bridge_connect_timeout_seconds: float = Field(gt=0)
    bridge_reconnect_min_seconds: int = Field(ge=0)
    bridge_reconnect_max_seconds: int = Field(gt=0)
    #: Verify the bridge TLS certificate. Live market data and order/trade
    #: updates flow over this connection with real-money implications, so
    #: verification is on by default. Set ``BRIDGE_TLS_VERIFY=false`` only
    #: for a local MITM-debugging session, never in production.
    bridge_tls_verify: bool

    # ---------------- Risk ----------------
    max_gross_exposure: float
    max_position_notional: float
    max_daily_loss: float
    max_open_positions: int
    allow_short: bool
    square_off_time: str

    # ---------------- Application store (control plane) ----------------
    #: Users, sessions, watchlists, and audit data store.
    app_db_url: str
    #: How long the health endpoint reuses the last database probe result.
    database_health_cache_seconds: float = Field(gt=0)
    #: Where nightly backups go. Point it at another disk or a synced folder: a copy
    #: on the same drive does not survive that drive failing.
    backup_dir: str
    #: How many of the newest backups to keep.
    backup_keep: int = Field(gt=0)
    options_dataset_dir: str
    options_dataset_max_bytes: int
    options_index_contracts_path: str
    options_max_quote_age_seconds: float
    options_forward_config_path: str
    #: Self-registration. Off by default — a trading platform should not accept
    #: strangers. The first account is created through the bootstrap route.
    allow_signup: bool
    #: Session lifetime. A trading session is long, so this is generous.
    session_ttl_hours: int
    #: Require a session for the API. Loopback requests in ``dev`` are exempt
    #: while no account exists yet, so the single-operator workflow keeps working.
    auth_required: bool
    #: Key material for encrypting secrets at rest (broker credentials, TOTP
    #: seeds). Set ``FORWARD_SECRET_KEY`` or create the key file at
    #: ``forward_secret_key_file`` before using secret storage.
    forward_secret_key: str
    forward_secret_key_file: str
    forward_audit_path: str
    login_max_attempts: int
    login_lockout_minutes: int
    #: Require a second factor for admin/owner accounts. Off by default so a
    #: fresh install is usable; turn it on for anything reachable off-host.
    require_mfa_for_privileged: bool
    rate_limit_per_minute: int
    rate_limit_login_per_minute: int

    # ---------------- API ----------------
    api_host: str
    api_port: int
    cors_allowed_origins: list[str]

    # ---------------- Alerts (notify-only) ----------------
    telegram_bot_token: str
    telegram_chat_id: str
    fast2sms_api_key: str  # SMS slot: paste key to activate real SMS
    discord_webhook_url: str
    slack_webhook_url: str

    # ---- Analytics and service policies ----
    analytics_max_aggregate_rows: int
    analytics_min_sample: int
    forward_check_min_trades: int
    forward_check_keep_share: float
    forward_check_win_tolerance_points: float
    dashboard_summary_ttl_seconds: float
    dashboard_breadth_ttl_seconds: float
    dashboard_broker_read_workers: int
    learning_min_sample: int
    learning_forward_window_days: int
    learning_daily_min_observations: int
    learning_p_adjusted_cutoff: float
    learning_confidence_strong: float
    learning_confidence_other: float
    daily_learning_confidence_other: float
    learning_dataset_max_rows: int
    learning_dataset_ttl_seconds: float = Field(gt=0)
    learning_dataset_refresh_ttl_seconds: float = Field(gt=0)
    learning_market_context_thresholds: dict[str, dict[str, float]]
    learning_atr_regime_thresholds: dict[str, float]
    learning_missing_feature_display_limit: int = Field(gt=0)
    pnl_calendar_backfill_sessions: int = Field(gt=0)
    track_record_default_days: int
    track_record_max_days: int
    track_record_days_options: list[int]
    track_record_win_rate_good_min_pct: float
    track_record_win_rate_warn_min_pct: float
    track_record_drawdown_meter_full_scale_pct: float
    learning_max_window_days: int
    auth_max_token_days: int
    backtest_uncertainty_paths: int
    backtest_uncertainty_block: int
    backtest_uncertainty_seed: int
    backtest_max_curve_points: int = Field(gt=0)
    portfolio_warn_at_pct_of_limit: float
    signal_explorer_default_page_size: int
    signal_explorer_max_page_size: int
    market_intel_default_page_size: int
    market_intel_max_page_size: int
    screener_request_interval_max_minutes: int
    screener_frame_cache_seconds: float
    backtest_uncertainty_paths_min: int
    backtest_uncertainty_paths_max: int
    backtest_uncertainty_block_min: int
    backtest_uncertainty_block_max: int
    options_fee_percent_max: float
    options_slippage_max_bps: float
    learning_sample_max_limit: int
    learning_score_bands: list[dict[str, object]]
    learning_small_sample: int
    learning_confidence_95: float
    learning_confidence_90: float
    learning_analysis_ready_n: int
    learning_recent_days: int
    learning_abnormal_return_pct: float
    ui_polling: dict[str, object]
    ui_screener: dict[str, object]
    ui_backtest: dict[str, object]
    parameter_screen_config: dict[str, object]
    ui_validation: dict[str, object]
    ui_market_mood: dict[str, object]
    ui_market_intelligence: dict[str, object]
    ui_learning: dict[str, object]
    ui_alerts: dict[str, object]
    ui_toast: dict[str, object]
    ui_symbol_picker: dict[str, object]
    live_tick_reconnect_delay_ms: int
    market_depth_rows_per_side: int
    command_palette_shortcut: str
    animated_number_duration_ms: int
    ui_time_fresh_minutes: int
    ui_time_stale_hours: int
    ui_relative_time: dict[str, object]
    ui_clock_hour12: bool
    stall_sma_period: int
    stall_return_period: int
    stall_short_return_period: int
    stall_high_period: int
    stall_range_short_period: int
    stall_range_baseline_period: int
    stall_rsi_period: int
    stall_rsi_shift_period: int
    stall_volume_short_period: int
    stall_volume_baseline_period: int
    stall_run_return_pct: float
    stall_extension_pct: float
    stall_near_high_distance_pct: float
    stall_flat_return_pct: float
    stall_flat_range_ratio: float
    stall_run_score_cap_pct: float
    stall_run_score_points: float
    stall_flat_return_score_points: float
    stall_flat_range_score_points: float
    stall_range_ratio_score_cap: float
    stall_momentum_rsi_minimum: float
    stall_momentum_rsi_drop: float
    stall_momentum_drop_points: float
    stall_momentum_level_points: float
    stall_volume_ratio_maximum: float
    stall_low_volume_points: float
    stall_upper_wick_minimum: float
    stall_upper_wick_period: int
    stall_upper_wick_points: float
    stall_score_threshold: float
    stall_score_maximum: float
    watch_range_quiet_ratio: float
    watch_protect_at_gain_pct: float
    watch_trail_below_high_pct: float
    watch_lagging_rs_pct: float
    insights_stale_after_days: int
    insights_cache_ttl_seconds: float
    insights_default_settings: dict[str, object]
    signal_scan_lookback_days: int
    data_status_bar_final_hour: int
    data_status_bar_final_minute: int
    runner_interval_seconds: float
    runner_lookback_days: int
    runner_min_sleep_seconds: float
    runner_market_wait_seconds: float
    deployment_symbol_retry_seconds: float
    market_quote_max_age_seconds: float
    paper_health_recent_sessions: int
    paper_health_signal_cache_ttl_seconds: float
    paper_health_result_ttl_seconds: float
    reconcile_quantity_tolerance: float
    reconcile_cash_tolerance: float
    screener_min_interval_minutes: int
    screener_max_lines: int
    strategy_definition_max_bytes: int
    backtest_worker_count: int
    backtest_shutdown_timeout_seconds: float
    experiment_curve_max_points: int = Field(gt=0)
    experiment_result_decimals: int = Field(ge=0)
    attribution_row_limit: int
    attribution_signal_match_window_seconds: float
    signal_scan_max_candidates: int
    paper_average_volume_days: int
    attribution_slippage_high_bps: float
    attribution_slippage_low_bps: float
    attribution_favorable_mfe_r: float
    attribution_high_mae_r: float
    attribution_risk_wide_factor: float
    attribution_risk_tight_factor: float
    attribution_capture_early_pct: float
    attribution_giveback_late_pct: float
    attribution_giveback_min_peak_r: float
    attribution_sizing_oversize_pct: float
    attribution_sizing_undersize_pct: float
    attribution_context_positive_score: float
    attribution_context_negative_score: float

    # ---- Chart surface ----
    chart_default_symbol: str
    chart_exchange: str
    chart_exchange_label: str
    chart_pine_presets_path: str
    chart_pine_template: dict[str, str]
    chart_default_timeframe: str
    chart_default_range: str
    chart_timeframes: list[str]
    chart_ranges: list[dict[str, object]]
    chart_indicators: list[dict[str, object]]
    chart_search_debounce_ms: int
    chart_search_min_query_length: int
    chart_symbol_search_limit: int
    options_symbol_search_limit: int
    watchlist_quote_poll_ms: int
    watchlist_quote_mode: str
    watchlist_search_limit: int
    watchlist_search_debounce_ms: int
    watchlist_quote_debounce_ms: int
    watchlist_default_exchange: str
    ui_locale: str
    ui_timezone: str
    ui_currency: str
    ui_date_format: str
    ui_number_decimals: int
    page_sizes: dict[str, int]

    # ---- Cost and execution models ----
    commission_model_per_share: float
    commission_model_per_contract: float
    commission_model_min_per_order: float
    commission_model_max_pct_of_trade: float
    commission_model_pct_of_notional: float
    indian_brokerage_per_order: float
    indian_brokerage_pct: float
    indian_delivery_stt_pct: float
    indian_delivery_stamp_pct_buy: float
    indian_exchange_pct: float
    indian_sebi_pct: float
    indian_gst_pct: float
    indian_delivery_dp_per_sell: float
    indian_intraday_dp_per_sell: float
    indian_intraday_stt_pct_sell: float
    indian_intraday_stamp_pct_buy: float
    slippage_default_bps: float
    slippage_default_ticks: float

    @property
    def is_live(self) -> bool:
        return self.env == "live"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def research_data_root() -> Path:
    """Return the configured research-data folder as an absolute path.

    Relative paths use the project root so commands behave the same regardless
    of the shell's current directory.

    Returns:
        Absolute path to the configured research-data folder.
    """
    configured = Path(get_settings().research_data_directory)
    if not configured.is_absolute():
        configured = PROJECT_ROOT / configured
    return configured.resolve()


def market_calendar_path() -> Path:
    """Return the explicitly configured exchange calendar file path.

    Returns:
        Absolute path to the configured market-calendar data file.
    """
    configured = Path(get_settings().market_calendar_path)
    if not configured.is_absolute():
        configured = PROJECT_ROOT / configured
    return configured.resolve()


def secret_key_path() -> Path:
    """Return the configured file path for locally stored encryption keys.

    Relative paths use the project root, matching SQLite and research data.

    Returns:
        Absolute path to the local encryption key file.
    """
    configured = Path(get_settings().forward_secret_key_file)
    if not configured.is_absolute():
        configured = PROJECT_ROOT / configured
    return configured.resolve()


def backup_directory() -> Path:
    """Return the configured backup folder as an absolute path.

    Returns:
        Absolute path where backup archives are stored.
    """
    configured = Path(get_settings().backup_dir)
    if not configured.is_absolute():
        configured = PROJECT_ROOT / configured
    return configured.resolve()


def audit_log_path() -> Path:
    """Return the configured local JSONL audit path.

    Returns:
        The configured audit file path.
    """
    configured = Path(get_settings().forward_audit_path)
    if not configured.is_absolute():
        configured = PROJECT_ROOT / configured
    return configured.resolve()
