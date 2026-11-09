"""Market-context performance calculations for learning reports."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from forward.core.learning_evidence import is_forward_grade, row_grade
from forward.research import learning_stats as stats
from forward.services.learning_analysis import matches_strategy


def query_market_context_performance(
    rows: list[dict[str, Any]],
    *,
    strategy: str | None,
    condition_axis: str,
    condition_value: str | None,
    grades: Iterable[str] | None,
    resolved_metric: str,
    metric_note: str | None,
    min_sample: int,
    bucket_thresholds: Mapping[str, Mapping[str, float]],
) -> dict[str, Any]:
    """Summarize trade outcomes by one market-context condition.

    Args:
        rows: Learning dataset rows to analyze.
        strategy: Optional strategy name or identifier.
        condition_axis: Dataset field or supported market-context alias.
        condition_value: Optional bucket label to select.
        grades: Optional evidence grades to include.
        resolved_metric: Outcome field selected by the learning service.
        metric_note: Explanation when the requested metric was resolved.
        min_sample: Minimum bucket size before the result is unsuppressed.
        bucket_thresholds: Configured numeric cutoffs for market-context labels.

    Returns:
        Baseline and per-bucket performance statistics with evidence counts.
    """
    if strategy:
        rows = [row for row in rows if matches_strategy(row, strategy)]
    if grades:
        wanted_grades = {str(grade).strip().lower() for grade in grades}
        rows = [row for row in rows if row_grade(row) in wanted_grades]
    rows = [row for row in rows if row.get("exit_ts") is not None]

    axis_field_map = {
        "market_regime": "market_regime",
        "regime": "market_regime",
        "volatility_regime": "volatility_regime",
        "stock_rs": "stock_relative_strength",
        "stock_rs_bucket": "stock_relative_strength",
        "sector_strength": "sector_relative_strength",
        "sector_strength_bucket": "sector_relative_strength",
        "market_breadth": "market_breadth",
        "breadth_bucket": "market_breadth",
        "nifty_trend": "nifty_trend_pct",
        "nifty_trend_bucket": "nifty_trend_pct",
    }

    def bucket_label(row: dict[str, Any]) -> str | None:
        """Return the established label for one axis value."""
        field = axis_field_map.get(condition_axis, condition_axis)
        raw = row.get(field)
        if raw is None:
            return None

        try:
            value = float(raw)
        except (TypeError, ValueError):
            if condition_axis in {
                "stock_rs",
                "stock_rs_bucket",
                "sector_strength",
                "sector_strength_bucket",
                "market_breadth",
                "breadth_bucket",
                "nifty_trend",
                "nifty_trend_bucket",
            }:
                return None
            return str(raw)

        if condition_axis in ("stock_rs", "stock_rs_bucket"):
            cutoffs = bucket_thresholds["stock_relative_strength"]
            if value > cutoffs["strong"]:
                return "strongly_outperforming"
            if value > cutoffs["outperforming"]:
                return "outperforming"
            if value > cutoffs["underperforming"]:
                return "underperforming"
            return "strongly_underperforming"
        if condition_axis in ("sector_strength", "sector_strength_bucket"):
            cutoffs = bucket_thresholds["sector_relative_strength"]
            if value > cutoffs["strong"]:
                return "strong_sector"
            if value > cutoffs["mild_outperformer"]:
                return "mild_outperformer"
            if value > cutoffs["mild_laggard"]:
                return "mild_laggard"
            return "weak_sector"
        if condition_axis in ("market_breadth", "breadth_bucket"):
            cutoffs = bucket_thresholds["market_breadth"]
            if value >= cutoffs["high"]:
                return "high_breadth"
            if value >= cutoffs["normal"]:
                return "normal_breadth"
            return "low_breadth"
        if condition_axis in ("nifty_trend", "nifty_trend_bucket"):
            cutoffs = bucket_thresholds["nifty_trend"]
            if value > cutoffs["uptrend"]:
                return "uptrend"
            if value > cutoffs["downtrend"]:
                return "sideways"
            return "downtrend"
        return str(raw)

    by_bucket: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        label = bucket_label(row)
        if label is not None:
            by_bucket.setdefault(label, []).append(row)

    def metric_value(row: dict[str, Any]) -> float | None:
        """Read a numeric outcome from one row."""
        value = row.get(resolved_metric)
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def bucket_stats(label: str, bucket_rows: list[dict[str, Any]]) -> dict[str, Any]:
        """Summarize one bucket and identify whether it has enough evidence."""
        values = [value for row in bucket_rows if (value := metric_value(row)) is not None]
        forward_count = sum(1 for row in bucket_rows if is_forward_grade(row_grade(row)))
        in_sample_count = len(bucket_rows) - forward_count
        summary = stats.summarise(values) if values else None
        wins = [value for value in values if value > 0]
        losses = [value for value in values if value <= 0]
        win_rate = len(wins) / len(values) if values else None
        gross_win = sum(wins) if wins else 0.0
        gross_loss = abs(sum(losses)) if losses else 0.0
        profit_factor = gross_win / gross_loss if gross_loss > 0 else None

        return {
            "label": label,
            "n": len(values),
            "n_forward": forward_count,
            "n_in_sample": in_sample_count,
            "suppressed": len(values) < min_sample,
            "mean": summary.mean if summary else None,
            "median": summary.median if summary else None,
            "ci_low": summary.mean_ci[0] if summary and summary.mean_ci else None,
            "ci_high": summary.mean_ci[1] if summary and summary.mean_ci else None,
            "win_rate": round(win_rate * 100, 2) if win_rate is not None else None,
            "profit_factor": round(profit_factor, 3) if profit_factor is not None else None,
            "evidence_note": (
                "forward"
                if forward_count >= min_sample
                else "insufficient_forward_observations"
                if forward_count > 0
                else "in_sample_only"
            ),
        }

    all_bucket_stats = {
        label: bucket_stats(label, bucket_rows)
        for label, bucket_rows in by_bucket.items()
    }
    if condition_value is not None:
        filtered = {
            label: result
            for label, result in all_bucket_stats.items()
            if label == condition_value
        }
        requested_stat = all_bucket_stats.get(condition_value)
    else:
        filtered = all_bucket_stats
        requested_stat = None

    all_values = [
        value for row in rows if (value := metric_value(row)) is not None
    ]
    overall = stats.summarise(all_values) if all_values else None
    def sort_key(bucket: dict[str, Any]) -> tuple[bool, int, int]:
        """Sort reliable buckets first, then by forward and total sample size."""
        return (not bucket["suppressed"], bucket["n_forward"], bucket["n"])

    return {
        "strategy": strategy or "ALL",
        "condition_axis": condition_axis,
        "condition_value": condition_value,
        "metric": resolved_metric,
        "metric_note": metric_note,
        "min_sample_floor": min_sample,
        "total_rows_scanned": len(rows),
        "rows_with_value": len(all_values),
        "baseline": {
            "n": len(all_values),
            "mean": overall.mean if overall else None,
            "median": overall.median if overall else None,
            "win_rate": (
                round(sum(1 for value in all_values if value > 0) / len(all_values) * 100, 2)
                if all_values
                else None
            ),
        },
        "buckets": sorted(filtered.values(), key=sort_key, reverse=True),
        "all_buckets": sorted(all_bucket_stats.values(), key=sort_key, reverse=True),
        "requested": requested_stat,
        "caveats": [
            *([f"metric resolved to {resolved_metric}: {metric_note}"] if metric_note else []),
            *(
                [f"no rows carry axis '{condition_axis}'; all buckets empty"]
                if not by_bucket
                else []
            ),
        ],
    }
