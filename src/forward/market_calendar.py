"""Read the configured NSE session schedule and holiday calendar."""

from __future__ import annotations

import json
from datetime import date, datetime
from datetime import time as clock_time
from zoneinfo import ZoneInfo

from forward.config.settings import get_settings, market_calendar_path
from forward.core.calendar_ports import MarketCalendarPort


class NSEMarketCalendar:
    """NSE session and holiday calendar loaded from configured data.

    Args:
        holidays: Dates listed as exchange holidays.
        coverage_start: First date for which weekdays are confirmed.
        coverage_end: Last date for which weekdays are confirmed.
        market_open: Start of the regular trading session.
        market_close: End of the regular trading session.
        timezone: Exchange timezone used to interpret session dates.
    """

    def __init__(
        self,
        *,
        holidays: set[date],
        coverage_start: date,
        coverage_end: date,
        market_open: clock_time,
        market_close: clock_time,
        timezone: ZoneInfo,
    ) -> None:
        if coverage_end < coverage_start:
            raise ValueError("market calendar coverage end must not be before its start")
        if market_close <= market_open:
            raise ValueError("market close must be after market open")
        self.holidays = holidays
        self.coverage_start = coverage_start
        self.coverage_end = coverage_end
        self.market_open = market_open
        self.market_close = market_close
        self.timezone = timezone

    def is_holiday(self, dt: date | datetime) -> bool | None:
        """Return holiday status, or ``None`` when weekday coverage is unknown.

        Args:
            dt: Date to check.

        Returns:
            ``True`` for weekends or configured holidays, ``False`` for a
            covered trading weekday, or ``None`` outside configured coverage.
        """
        day = dt.date() if isinstance(dt, datetime) else dt
        if day.weekday() >= 5:
            return True
        if not self._covered(day):
            return None
        return day in self.holidays

    def is_trading_day(self, dt: date | datetime) -> bool | None:
        """Return whether the exchange trades on the date, or ``None`` if unknown."""
        holiday = self.is_holiday(dt)
        return None if holiday is None else not holiday

    def session_bounds(self, dt: date | datetime) -> tuple[datetime, datetime]:
        """Return the regular session timestamps for a date.

        Args:
            dt: Date whose session bounds are requested.

        Returns:
            Timezone-aware session start and end.
        """
        day = dt.date() if isinstance(dt, datetime) else dt
        return (
            datetime.combine(day, self.market_open, tzinfo=self.timezone),
            datetime.combine(day, self.market_close, tzinfo=self.timezone),
        )

    def is_market_open(self, dt: datetime | None = None) -> bool | None:
        """Return session status, or ``None`` when holiday coverage is missing.

        Args:
            dt: Time to check, or the current exchange-local time.

        Returns:
            Whether the regular session is open, or ``None`` when unknown.
        """
        local = self._local_time(dt)
        trading_day = self.is_trading_day(local.date())
        if trading_day is None:
            return None
        return trading_day and self.market_open <= local.time() <= self.market_close

    def reason_closed(self, dt: datetime | None = None) -> str | None:
        """Explain a closed session or missing holiday-calendar coverage.

        Args:
            dt: Time to describe, or the current exchange-local time.

        Returns:
            Closure reason, ``None`` when open, or an explicit coverage message.
        """
        local = self._local_time(dt)
        day = local.date()
        if day.weekday() >= 5:
            return f"weekend ({local.strftime('%A')})"
        if not self._covered(day):
            return (
                "holiday calendar unavailable for "
                f"{day.isoformat()} (coverage {self.coverage_start} through {self.coverage_end})"
            )
        if day in self.holidays:
            return f"exchange holiday ({day.isoformat()})"
        if local.time() < self.market_open:
            return f"pre-market (opens at {self.market_open:%H:%M} {self.timezone.key})"
        if local.time() > self.market_close:
            return f"post-market (closed at {self.market_close:%H:%M} {self.timezone.key})"
        return None

    def status_payload(self, now: datetime | None = None) -> dict[str, object]:
        """Return the schedule and current market state for API clients.

        Args:
            now: Time to report, or the current exchange-local time.

        Returns:
            Configured window, current state, reason, and calendar coverage.
        """
        local = self._local_time(now)
        open_now = self.is_market_open(local)
        reason = self.reason_closed(local)
        if open_now is None:
            status = "calendar_unavailable"
        elif open_now:
            status = "open"
        elif local.weekday() >= 5:
            status = "weekend"
        elif local.date() in self.holidays:
            status = "exchange_holiday"
        elif local.time() < self.market_open:
            status = "pre_market"
        else:
            status = "post_market"

        settings = get_settings()
        return {
            "exchange": "NSE",
            "timezone": self.timezone.key,
            "window": {
                "opens_at": self.market_open.strftime("%H:%M"),
                "closes_at": self.market_close.strftime("%H:%M"),
            },
            "order_window": {
                "opens_at": settings.order_window_open.strftime("%H:%M"),
                "closes_at": self.market_close.strftime("%H:%M"),
            },
            "open": open_now,
            "status": status,
            "reason": reason,
            "holiday_calendar": {
                "available": self._covered(local.date()),
                "coverage_start": self.coverage_start.isoformat(),
                "coverage_end": self.coverage_end.isoformat(),
            },
        }

    def _covered(self, day: date) -> bool:
        return self.coverage_start <= day <= self.coverage_end

    def _local_time(self, dt: datetime | None) -> datetime:
        if dt is None:
            return datetime.now(self.timezone)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=self.timezone)
        return dt.astimezone(self.timezone)


def get_market_calendar() -> MarketCalendarPort:
    """Load the market calendar from the configured file and session settings.

    Returns:
        Calendar backed by the configured holiday data and market hours.

    Raises:
        FileNotFoundError: If the configured calendar file does not exist.
        ValueError: If the calendar file is malformed or lacks required fields.
    """
    path = market_calendar_path()
    payload = json.loads(path.read_text(encoding="utf-8"))
    required = {"exchange", "timezone", "coverage_start", "coverage_end", "holidays"}
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValueError(f"market calendar must contain exactly {sorted(required)}: {path}")
    if payload["exchange"] != "NSE":
        raise ValueError(f"configured market calendar exchange must be NSE: {path}")
    if not isinstance(payload["holidays"], list):
        raise ValueError(f"market calendar holidays must be a list: {path}")
    settings = get_settings()
    return NSEMarketCalendar(
        holidays={date.fromisoformat(value) for value in payload["holidays"]},
        coverage_start=date.fromisoformat(payload["coverage_start"]),
        coverage_end=date.fromisoformat(payload["coverage_end"]),
        market_open=settings.market_open,
        market_close=settings.market_close,
        timezone=ZoneInfo(payload["timezone"]),
    )


def is_market_open(now: datetime | None = None) -> bool | None:
    """Check current market state from configured session and holiday data.

    Args:
        now: Time to check, or the current exchange-local time.

    Returns:
        Whether the market is open, or ``None`` when holiday coverage is missing.
    """
    return get_market_calendar().is_market_open(now)


__all__ = ["NSEMarketCalendar", "get_market_calendar", "is_market_open"]
