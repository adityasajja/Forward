"""Order qualified deployment entry candidates by their configured rank."""

from __future__ import annotations

from typing import Any

import pandas as pd

EntryCandidate = tuple[str, float, pd.DataFrame, Any, tuple[str, str, str]]


def prioritize_entry_candidates(
    candidates: list[EntryCandidate], rank_formula: str | None
) -> list[EntryCandidate]:
    """Return candidates in the order the deployment should consider them.

    Args:
        candidates: Qualified symbol, price, frame, signal, and action key rows.
        rank_formula: Optional formula used to rank candidate market frames.

    Returns:
        Candidates sorted by formula score, or by their existing gap score.

    Raises:
        FormulaError: If the configured formula cannot rank a candidate.
    """
    if not rank_formula:
        return sorted(candidates, key=lambda candidate: candidate[3].detail.get("gap_pct", 0.0))

    from forward.signals.formula import FormulaError, build_context, compile_formula

    ranker = compile_formula(rank_formula)

    def score(candidate: EntryCandidate) -> float:
        context = build_context(candidate[2])
        if not context:
            raise FormulaError(f"not enough market history to rank {candidate[0]}")
        return -ranker.eval_value(context)

    return sorted(candidates, key=score)
