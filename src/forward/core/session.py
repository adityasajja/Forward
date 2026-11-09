"""NSE session clocks. Naive historical timestamps are exchange-local (IST)."""

from datetime import datetime, time
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from forward.core.calendar_ports import MarketCalendarPort

IST = ZoneInfo("Asia/Kolkata")
DAILY_TIMEFRAME = "1d"
SESSION_ALIGNED_TIMEFRAME_MINUTES = {"1m": 1, "5m": 5, "15m": 15}
RUNNER_INTRADAY_TIMEFRAME_MINUTES = {
    **SESSION_ALIGNED_TIMEFRAME_MINUTES,
    "10m": 10,
    "30m": 30,
    "60m": 60,
}
STRATEGY_TIMEFRAMES = (DAILY_TIMEFRAME, *SESSION_ALIGNED_TIMEFRAME_MINUTES)


def clock(value: str) -> time:
    """Parse a market-session clock value.

    Args:
        value: Session time in ``HH:MM`` format.

    Returns:
        The parsed local time.

    Raises:
        ValueError: If the value is not a valid ``HH:MM`` time.
    """
    if not isinstance(value, str) or len(value) != 5:
        raise ValueError("Session times must use HH:MM")
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError as exc:
        raise ValueError("Session times must use HH:MM") from exc


def validate_session(
    entry_cutoff: str,
    square_off_time: str,
    timeframe: str | None = None,
    *,
    market_open: time,
    market_close: time,
) -> None:
    """Validate a strategy's entry and square-off times.

    Args:
        entry_cutoff: Last time the strategy may open a position.
        square_off_time: Time the strategy must close intraday positions.
        timeframe: Intraday interval used to align the two times, if supported.
        market_open: Configured start of the regular exchange session.
        market_close: Configured end of the regular exchange session.

    Raises:
        ValueError: If the times fall outside the session or are misaligned.
    """
    if not market_open < clock(entry_cutoff) < clock(square_off_time) < market_close:
        raise ValueError(
            f"Use {market_open:%H:%M} < entry cutoff < square-off < {market_close:%H:%M} IST"
        )
    minutes = SESSION_ALIGNED_TIMEFRAME_MINUTES.get(timeframe)
    open_minutes = market_open.hour * 60 + market_open.minute
    if minutes and any(
        (clock(value).hour * 60 + clock(value).minute - open_minutes) % minutes
        for value in (entry_cutoff, square_off_time)
    ):
        raise ValueError(
            f"Session times must align with {minutes}-minute bars from {market_open:%H:%M} IST"
        )


def local_time(stamp: datetime) -> time:
    """Return a timestamp's local market time.

    Naive timestamps are already treated as exchange-local time.

    Args:
        stamp: Timestamp to convert.

    Returns:
        The timestamp's time in India, without timezone metadata.
    """
    return local_datetime(stamp).time()


def local_datetime(stamp: datetime) -> datetime:
    """Return a naive India-local datetime for session comparisons.

    Naive timestamps already represent exchange-local time. Aware timestamps
    are converted to India time before the timezone metadata is removed.

    Args:
        stamp: Timestamp to convert.

    Returns:
        The timestamp in India-local time without timezone metadata.
    """
    if stamp.tzinfo is not None:
        return stamp.astimezone(IST).replace(tzinfo=None)
    return stamp


def is_market_session_open(
    stamp: datetime | None, *, calendar: MarketCalendarPort
) -> bool | None:
    """Return the configured market calendar's session status.

    Args:
        stamp: Timestamp to check, or None to use the current exchange-local time.
        calendar: Market-calendar port with the configured session window.

    Returns:
        Whether the exchange is open, or ``None`` when schedule coverage is unknown.
    """
    local = local_datetime(stamp) if stamp is not None else None
    return calendar.is_market_open(local)
