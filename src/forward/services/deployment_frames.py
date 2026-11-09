"""Project historical candles into the completed bars for a time boundary."""

from __future__ import annotations

from datetime import datetime

import pandas as pd


def completed_intraday_bars(
    history: pd.DataFrame | None,
    boundary: datetime,
    timeframe_minutes: int,
) -> tuple[pd.DataFrame | None, int]:
    """Return completed bars and whether the newest bar is behind.

    Args:
        history: Historical candle rows with a ``ts`` column.
        boundary: Start time of the bar that is currently forming.
        timeframe_minutes: Length of each candle in minutes.

    Returns:
        The completed bars and a freshness flag. The flag is ``1`` when no
        completed bar exists or the newest bar does not end at the boundary.
    """
    if history is None or history.empty:
        return None, 1

    stamps = pd.to_datetime(history["ts"])
    cutoff = pd.Timestamp(boundary)
    cutoff = (
        cutoff.tz_localize(None)
        if stamps.dt.tz is None
        else cutoff.tz_convert(stamps.dt.tz)
    )

    completed = (
        history.loc[stamps < cutoff]
        .drop_duplicates("ts", keep="last")
        .sort_values("ts")
        .reset_index(drop=True)
    )
    if completed.empty:
        return None, 1

    behind = int(
        pd.Timestamp(completed["ts"].iloc[-1])
        != cutoff - pd.Timedelta(minutes=timeframe_minutes)
    )
    return completed, behind
