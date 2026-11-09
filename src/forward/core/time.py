"""Shared time functions for values stored by the application."""

from __future__ import annotations

from datetime import UTC, date, datetime


def utcnow() -> datetime:
    """Return naive UTC for stable database timestamp storage.

    Returns:
        The current UTC time without timezone metadata.
    """
    return datetime.now(UTC).replace(tzinfo=None)


def timestamp_sort_key(value: date | datetime | str | None) -> str:
    """Return one stable text key for sorting saved timestamps.

    Args:
        value: A date, a datetime, a saved timestamp string, or no timestamp.

    Returns:
        An ISO date or timestamp string, or an empty string when missing.
    """
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value or ""
