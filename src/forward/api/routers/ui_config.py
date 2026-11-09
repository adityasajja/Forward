"""Safe, operator-configured display settings for the frontend."""

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from forward.config.settings import PROJECT_ROOT, get_settings

router = APIRouter(prefix="/api/v1", tags=["ui-config"])


@router.get("/ui-config")
def read_ui_config() -> dict[str, Any]:
    """Return display values that the browser must not guess locally."""
    settings = get_settings()
    pine_presets_path = Path(settings.chart_pine_presets_path)
    if not pine_presets_path.is_absolute():
        pine_presets_path = PROJECT_ROOT / pine_presets_path
    pine_presets = json.loads(pine_presets_path.read_text(encoding="utf-8"))
    if not isinstance(pine_presets, list) or any(
        not isinstance(preset, dict)
        or set(preset) != {"name", "description", "code"}
        or not all(isinstance(preset[key], str) for key in preset)
        for preset in pine_presets
    ):
        raise ValueError(f"Pine presets config must be a list of named scripts: {pine_presets_path}")
    return {
        "ui_config": {
            "locale": settings.ui_locale,
            "timezone": settings.ui_timezone,
            "currency": settings.ui_currency,
            "date_format": settings.ui_date_format,
            "number_decimals": settings.ui_number_decimals,
            "clock": {"hour12": settings.ui_clock_hour12},
            "keyboard": {"command_palette_shortcut": settings.command_palette_shortcut},
            "motion": {"animated_number_duration_ms": settings.animated_number_duration_ms},
            "time_fresh_minutes": settings.ui_time_fresh_minutes,
            "time_stale_hours": settings.ui_time_stale_hours,
            "relative_time": settings.ui_relative_time,
            "toast": settings.ui_toast,
            "symbol_picker": settings.ui_symbol_picker,
            "live_ticks": {"reconnect_delay_ms": settings.live_tick_reconnect_delay_ms},
            "market_depth": {"rows_per_side": settings.market_depth_rows_per_side},
            "chart": {
                "default_symbol": settings.chart_default_symbol,
                "exchange": settings.chart_exchange,
                "exchange_label": settings.chart_exchange_label,
                "default_exchange": settings.chart_exchange,
                "pine_presets": pine_presets,
                "pine_template": settings.chart_pine_template,
                "default_timeframe": settings.chart_default_timeframe,
                "default_range": settings.chart_default_range,
                "timeframes": settings.chart_timeframes,
                "ranges": settings.chart_ranges,
                "indicators": settings.chart_indicators,
                "search_debounce_ms": settings.chart_search_debounce_ms,
                "search_min_query_length": settings.chart_search_min_query_length,
                "symbol_search_limit": settings.chart_symbol_search_limit,
            },
            "options": {
                "symbol_search_limit": settings.options_symbol_search_limit,
            },
            "watchlist": {
                "quote_poll_ms": settings.watchlist_quote_poll_ms,
                "quote_mode": settings.watchlist_quote_mode,
                "search_limit": settings.watchlist_search_limit,
                "search_debounce_ms": settings.watchlist_search_debounce_ms,
                "quote_debounce_ms": settings.watchlist_quote_debounce_ms,
                "default_exchange": settings.watchlist_default_exchange,
            },
            "track_record": {
                "default_days": settings.track_record_default_days,
                "max_days": settings.track_record_max_days,
                "days_options": settings.track_record_days_options,
                "win_rate_good_min_pct": settings.track_record_win_rate_good_min_pct,
                "win_rate_warn_min_pct": settings.track_record_win_rate_warn_min_pct,
                "drawdown_meter_full_scale_pct": settings.track_record_drawdown_meter_full_scale_pct,
            },
            "page_sizes": settings.page_sizes,
            "polling": settings.ui_polling,
            "screener": settings.ui_screener,
            "backtest": settings.ui_backtest,
            "validation": settings.ui_validation,
            "market_mood": settings.ui_market_mood,
            "market_intelligence": settings.ui_market_intelligence,
            "learning": settings.ui_learning,
            "alerts": settings.ui_alerts,
            "insights": {
                "stale_after_days": settings.insights_stale_after_days,
                "cache_ttl_seconds": settings.insights_cache_ttl_seconds,
                "default_settings": settings.insights_default_settings,
            },
        }
    }
