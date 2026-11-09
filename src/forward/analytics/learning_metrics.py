"""Shared metrics for portfolio and learning reports."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from forward.config.settings import get_settings

SCORE_BANDS: tuple[tuple[str, int, int], ...] = tuple(
    (str(row["label"]), int(row["low"]), int(row["high"]))
    for row in get_settings().learning_score_bands
)


def score_band(score: Any) -> str | None:
    """Return the declared band for a score.

    Args:
        score: Context score to place in a band.

    Returns:
        The matching band, or ``None`` when the score is missing or out of range.
    """
    try:
        value = int(score)
    except (TypeError, ValueError):
        return None
    for label, low, high in SCORE_BANDS:
        if low <= value <= high:
            return label
    return None


def max_drawdown(values: Sequence[float | None]) -> float | None:
    """Return the largest peak-to-trough fall in a cumulative metric.

    Values are added in order. Missing values are skipped. The result stays in
    the same unit as the input because scaling a noisy sample would imply more
    precision than it contains.

    Args:
        values: Ordered metric changes, such as per-trade returns.

    Returns:
        The largest fall rounded to four decimal places, or ``None`` when no
        values can be measured.
    """
    equity = 0.0
    peak = 0.0
    worst = 0.0
    seen = False
    for value in values:
        if value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        equity += number
        seen = True
        if equity > peak:
            peak = equity
        worst = max(worst, peak - equity)
    return round(worst, 4) if seen else None
