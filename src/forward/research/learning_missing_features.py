"""Explain features that are missing from the learning dataset."""

from __future__ import annotations

from typing import Any

from forward.core.learning_evidence import (
    SOURCE_BACKTEST,
    SOURCE_LIVE,
    SOURCE_PAPER,
    SOURCES,
)
from forward.research import learning_paper as paper
from forward.research.learning_axes import requested_but_unavailable
from forward.research.learning_enrich import MIN_BARS_SMA_SLOW


def missing_feature_registry(rows: list[dict[str, Any]]) -> dict[str, str]:
    """Explain why each feature is missing from the complete dataset.

    Args:
        rows: Dataset rows with per-row ``_missing`` feature names.

    Returns:
        A mapping from unavailable feature names to their causes.
    """
    by_source: dict[str, dict[str, int]] = {}
    for row in rows:
        source = str(row.get("source") or "unknown")
        for name in row.get("_missing") or []:
            by_source.setdefault(name, {})
            by_source[name][source] = by_source[name].get(source, 0) + 1

    def why(feature: str, per_source: dict[str, str]) -> str | None:
        """Join the causes for one feature in stable source order.

        Args:
            feature: Feature name to explain.
            per_source: Cause for each source that cannot provide the feature.

        Returns:
            The joined causes, or ``None`` when every source has the feature.
        """
        present = by_source.get(feature) or {}
        parts = [
            per_source[source]
            for source in (*SOURCES, "unknown")
            if source in present and source in per_source
        ]
        return "; ".join(parts) if parts else None

    reasons = dict(requested_but_unavailable())
    for feature, cause in paper.reasons().items():
        if by_source.get(feature):
            reasons.setdefault(feature, cause)

    shared = {
        "mfe": {
            SOURCE_BACKTEST: (
                "backtest_trades has no mfe column, so a backtest trade's "
                "intra-trade excursion was never recorded"
            ),
            SOURCE_PAPER: paper.REASON_NO_EXCURSION,
            SOURCE_LIVE: "the journal entry did not record mfe",
        },
        "mae": {
            SOURCE_BACKTEST: (
                "backtest_trades has no mae column, so a backtest trade's "
                "intra-trade excursion was never recorded"
            ),
            SOURCE_PAPER: paper.REASON_NO_EXCURSION,
            SOURCE_LIVE: "the journal entry did not record mae",
        },
        "slippage_bps": {
            SOURCE_BACKTEST: (
                "the backtest reports total slippage for the run, not per trade"
            ),
            SOURCE_PAPER: paper.REASON_NO_COSTS,
            SOURCE_LIVE: "the journal entry did not record slippage",
        },
    }
    for feature, per_source in shared.items():
        cause = why(feature, per_source)
        if cause:
            reasons[feature] = cause

    if by_source.get("sector"):
        reasons["sector"] = "the symbol is missing from the configured sector table"
    if by_source.get("relative_volume_from_reason"):
        reasons["relative_volume_from_signal"] = (
            "the strategy recorded no signal reason naming a volume multiple; "
            "the value is still computed from the price cache where history allows"
        )
    if by_source.get("trend_pct"):
        reasons["trend_pct"] = f"fewer than {MIN_BARS_SMA_SLOW} bars before the entry"
    if by_source.get("attribution_entry_quality"):
        attribution_reasons = {
            SOURCE_BACKTEST: (
                "backtest trades carry no journal episode, and the attribution "
                "layer is keyed on one"
            ),
            SOURCE_PAPER: (
                "the paper ledger records settled weeks rather than journal "
                "episodes, so there is no trade to attribute"
            ),
            SOURCE_LIVE: "the journal episode has not been attributed yet",
        }
        reasons["attribution_entry_quality"] = why(
            "attribution_entry_quality", attribution_reasons
        )

    return {name: cause for name, cause in reasons.items() if cause}
