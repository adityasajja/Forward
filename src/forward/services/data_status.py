"""Calculate the age of the market data used by the app."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from forward.config.settings import get_settings
from forward.core.calendar_ports import MarketCalendarPort
from forward.core.market_data_status_ports import MarketDataStatusPort
from forward.market_calendar import IST

_settings = get_settings()
BAR_FINAL_AFTER = time(
    _settings.data_status_bar_final_hour,
    _settings.data_status_bar_final_minute,
)

_STATUS_ORDER = {"fresh": 0, "late": 1, "stale": 2, "missing": 3}


def expected_session(now: datetime, calendar: MarketCalendarPort) -> date:
    """Return the latest trading day whose daily bar should be ready.

    Args:
        now: Current time.
        calendar: Exchange calendar used to find trading days.

    Returns:
        The expected latest completed session.
    """
    local = now.astimezone(IST) if now.tzinfo else now.replace(tzinfo=IST)
    expected = local.date()
    if not (calendar.is_trading_day(expected) and local.time() >= BAR_FINAL_AFTER):
        expected -= timedelta(days=1)
    while not calendar.is_trading_day(expected):
        expected -= timedelta(days=1)
    return expected


def sessions_behind(as_of: date, expected: date, calendar: MarketCalendarPort) -> int:
    """Count trading days after the stored date through the expected date.

    Args:
        as_of: Latest date in the stored data.
        expected: Latest session expected to be stored.
        calendar: Exchange calendar used to count trading days.

    Returns:
        Number of missing trading sessions.
    """
    count = 0
    current = as_of
    while current < expected:
        current += timedelta(days=1)
        if calendar.is_trading_day(current):
            count += 1
    return count


def classify(sessions: int | None) -> str:
    """Map missing-session count to the UI status name."""
    if sessions is None:
        return "missing"
    return "fresh" if sessions == 0 else "late" if sessions == 1 else "stale"


def build_status(
    source: MarketDataStatusPort,
    now: datetime | None = None,
    *,
    calendar: MarketCalendarPort,
) -> dict[str, Any]:
    """Build market-data freshness from stored facts and the exchange calendar.

    Args:
        source: Port that reads current cache facts.
        now: Time used to decide which session should be complete.
        calendar: Exchange calendar used to count trading days.

    Returns:
        Freshness and provenance for the dashboard.
    """
    now = now if now is not None else datetime.now(IST)
    expected = expected_session(now, calendar)
    snapshot = source.snapshot()

    def source_row(
        source_id: str,
        label: str,
        role: str,
        origin: str | None,
        as_of: date | None,
    ) -> dict[str, Any]:
        sessions = sessions_behind(as_of, expected, calendar) if as_of else None
        return {
            "id": source_id,
            "label": label,
            "role": role,
            "origin": origin,
            "as_of": as_of.isoformat() if as_of else None,
            "sessions_behind": sessions,
            "status": classify(sessions),
        }

    sources = [
        source_row(
            "prices",
            "Stock prices",
            "Charts, scans, backtests and every paper valuation",
            None,
            snapshot.stock_as_of,
        ),
        source_row(
            "index",
            "Nifty 50",
            "The yardstick every result is compared with",
            snapshot.index_origin,
            snapshot.index_as_of,
        ),
    ]

    overall = max((row["status"] for row in sources), key=_STATUS_ORDER.__getitem__)
    return {
        "expected_session": expected.isoformat(),
        "checked_at": now.isoformat(timespec="seconds"),
        "market_open": calendar.is_market_open(now),
        "overall": overall,
        "sources": sources,
        "samples": snapshot.sample_count,
        "quality": snapshot.quality,
        "demo": snapshot.demo,
    }
