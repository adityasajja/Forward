"""Timestamp helpers shared by backtest execution and result formatting."""

from __future__ import annotations

from datetime import datetime

import pandas as pd


def timestamp_key(value: object) -> str:
    """Return a stable timestamp key for trade and signal records.

    Args:
        value: A Python or pandas timestamp value.

    Returns:
        The timestamp in ISO format.

    Raises:
        ValueError: If the value is missing or cannot be parsed as a timestamp.
    """
    if value is None:
        raise ValueError("timestamp is required for matching")
    if isinstance(value, datetime):
        if pd.isna(value):
            raise ValueError("timestamp is required for matching")
        stamp = value
    else:
        try:
            parsed = pd.Timestamp(value)
        except (ValueError, TypeError, OverflowError):
            raise ValueError(f"invalid timestamp for matching: {value!r}") from None
        if pd.isna(parsed):
            raise ValueError(f"invalid timestamp for matching: {value!r}")
        stamp = parsed.to_pydatetime()
    return stamp.isoformat()
