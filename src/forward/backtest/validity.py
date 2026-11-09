"""Explain data and trading assumptions that limit a backtest result."""

from __future__ import annotations

import pandas as pd

from forward.analytics.history_quality import (
    MAX_DAILY_JUMP,
    MAX_GAP_DAYS,
    gap_findings,
    jump_findings,
)


def _examples(items: list[str], limit: int = 4) -> str:
    """Format a short sample of findings.

    Args:
        items: Complete list of findings.
        limit: Maximum number of findings to show in the warning.

    Returns:
        A comma-separated sample and a count of the remaining findings.
    """
    extra = len(items) - limit
    suffix = f" and {extra} more" if extra > 0 else ""
    return ", ".join(items[:limit]) + suffix


def validity_warnings(
    frames: dict[str, pd.DataFrame], *, allow_short: bool, cost_model: str
) -> list[str]:
    """Explain data gaps and trading rules that limit a backtest result.

    Args:
        frames: Symbol-keyed price frames used by the backtest.
        allow_short: Whether the strategy can hold short positions.
        cost_model: Name of the cost model used by the run.

    Returns:
        Warnings about suspect prices, missing history, survivorship, and
        unsupported overnight cash-equity shorts.
    """
    notes = []
    jumps = jump_findings(frames)
    if jumps:
        notes.append(
            f"{len(jumps)} symbol(s) have a one-day close move over "
            f"{MAX_DAILY_JUMP:.0%} — likely an unadjusted split or bonus, "
            f"which fakes a crash or rally: {_examples(jumps)}. "
            "Check the price history before trusting trades in these names."
        )
    gaps = gap_findings(frames)
    if gaps:
        notes.append(
            f"{len(gaps)} symbol(s) have a gap of over {MAX_GAP_DAYS} days "
            f"inside their history (missing data, not a quiet market): "
            f"{_examples(gaps)}."
        )
    notes.append(
        "Survivorship: the universe is built from names that exist today. "
        "Companies delisted or collapsed during this period are not in it, so "
        "results are flattered, most for long-only strategies over long windows."
    )
    if allow_short and cost_model == "india_delivery":
        notes.append(
            "Shorting is on but costs are delivery rates. In India a cash-equity "
            "short must be squared off the same day (intraday rates, different "
            "STT), so overnight shorts here cannot be done in practice."
        )
    return notes


__all__ = ["validity_warnings"]
