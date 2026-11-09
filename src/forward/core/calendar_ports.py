"""Ports for exchange-calendar behavior used by application services."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class MarketCalendarPort(Protocol):
    """Read trading dates and session state from an exchange calendar."""

    def is_trading_day(self, dt: date | datetime) -> bool | None:
        """Return whether the exchange trades, or ``None`` when unknown."""

    def is_holiday(self, dt: date | datetime) -> bool | None:
        """Return holiday state, or ``None`` when weekday coverage is unknown."""

    def is_market_open(self, dt: datetime | None = None) -> bool | None:
        """Return whether the exchange is open, or ``None`` when unknown."""

    def session_bounds(self, dt: date | datetime) -> tuple[datetime, datetime]:
        """Return the open and close timestamps for the exchange session."""

    def reason_closed(self, dt: datetime | None = None) -> str | None:
        """Return closure or coverage reason, or ``None`` when open."""

    @property
    def market_open(self) -> time:
        """Configured start of the regular session."""

    @property
    def market_close(self) -> time:
        """Configured end of the regular session."""

    def status_payload(self, now: datetime | None = None) -> dict[str, Any]:
        """Return market window, status, reason, and holiday-calendar coverage."""
