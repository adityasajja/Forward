"""Find large price jumps and missing stretches in historical bars."""

from __future__ import annotations

import pandas as pd

MAX_DAILY_JUMP = 0.35
MAX_GAP_DAYS = 12


def jump_findings(frames: dict[str, pd.DataFrame]) -> list[str]:
    """Return symbols and dates with unusually large daily close changes.

    Args:
        frames: Symbol-keyed frames with a datetime index and close column.

    Returns:
        One readable finding for each symbol with a close change above the
        configured daily limit.
    """
    findings = []
    for symbol, frame in frames.items():
        if frame is None or len(frame) < 2 or "close" not in frame:
            continue
        changes = frame["close"].astype(float).pct_change().abs()
        unusual = changes[changes > MAX_DAILY_JUMP]
        if not unusual.empty:
            timestamp = unusual.idxmax()
            findings.append(
                f"{symbol} {pd.Timestamp(timestamp).date()} ({unusual.max():.0%})"
            )
    return findings


def gap_findings(frames: dict[str, pd.DataFrame]) -> list[str]:
    """Return symbols and dates with large gaps between historical bars.

    Args:
        frames: Symbol-keyed frames with a datetime index.

    Returns:
        One readable finding for each symbol with a gap above the configured
        calendar-day limit.
    """
    findings = []
    for symbol, frame in frames.items():
        if frame is None or len(frame) < 2:
            continue
        index = pd.DatetimeIndex(frame.index)
        gaps = pd.Series(index[1:] - index[:-1], index=index[1:])
        large_gaps = gaps[gaps > pd.Timedelta(days=MAX_GAP_DAYS)]
        if not large_gaps.empty:
            findings.append(
                f"{symbol} {large_gaps.index[0].date()} "
                f"({large_gaps.iloc[0].days} days)"
            )
    return findings
