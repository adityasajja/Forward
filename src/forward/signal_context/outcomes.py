"""Pure calculations for resolved signal-context outcomes."""

from __future__ import annotations

from typing import Any


def episode_return_pct(episode: dict[str, Any]) -> float | None:
    """Calculate signed price return for a journal episode.

    Args:
        episode: Journal fields that include entry price, exit price, and side.

    Returns:
        Price return in percent rounded to four decimal places, or ``None`` when
        the episode does not have usable prices.
    """
    entry = episode.get("entry_price")
    exit_price = episode.get("exit_price")
    if entry is None or exit_price is None:
        return None
    try:
        entry = float(entry)
        exit_price = float(exit_price)
    except (TypeError, ValueError):
        return None
    if entry <= 0:
        return None
    change = (exit_price / entry - 1.0) * 100.0
    if str(episode.get("side") or "").upper() in ("SELL", "S"):
        change = -change
    return round(change, 4)
