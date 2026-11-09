"""Learning evidence dataset and its stable table shape."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from forward.core.learning_evidence import (
    EVIDENCE_CLASSES,
    GRADE_FORWARD,
    GRADE_IN_SAMPLE,
    GRADES,
    SOURCES,
    is_forward_grade,
    row_grade,
)

OUTCOME_METRICS = ("net_pnl", "return_pct")


DATASET_COLUMNS = (
    # identity
    "source",
    "evidence_class",
    #: The direction, derived from ``evidence_class`` by :func:`grade_of` and
    #: written by the builder rather than left to each consumer to compute.
    #: ``evidence_class`` answers "where did this come from"; this answers the
    #: only question a finding depends on.
    "evidence_grade",
    #: Redundant with ``evidence_grade`` on purpose. Every consumer that wants to
    #: know "is this evidence?" would otherwise re-implement the membership test,
    #: and the first one to get it wrong would present an in-sample number as a
    #: finding — which is the single failure this dataset exists to prevent.
    "is_forward",
    #: Why this row carries the grade it does. A grade with no stated basis is a
    #: label a reader has to take on trust, and the whole point of the column is
    #: that it should not have to be taken on trust.
    "evidence_note",
    "source_ref",
    "trade_ref",
    #: The signal that raised the trade, and the opening order behind it. Both
    #: stay ``None`` when the trade cannot be linked back — an unlinked row is
    #: reported by data-quality monitoring, never silently dropped and never
    #: filled with a placeholder.
    "signal_id",
    "opening_order_id",
    #: The context the signal engine recorded for that signal, kept exactly as
    #: recorded — never recomputed here, so this table and the signal-context
    #: record cannot disagree.
    "context_score",
    "context_model_version",
    "context_class",
    "strategy_key",
    "strategy_id",
    "strategy_version",
    "engine_key",
    "symbol",
    "sector",
    "direction",
    # timing
    "entry_ts",
    "exit_ts",
    "duration_days",
    "day_of_week",
    "time_of_day",
    # outcome
    "quantity",
    "entry_price",
    "exit_price",
    "gross_pnl",
    "commission",
    "net_pnl",
    "return_pct",
    "exit_reason",
    # excursions — journal only
    "mfe",
    "mae",
    "slippage_bps",
    # entry conditions
    "signal_reason",
    "setup",
    "rsi",
    "sma_fast",
    "sma_slow",
    "sma_long",
    "prior_high",
    "signal_volume_multiple",
    "breakout_proximity_pct",
    # entry-time market context
    "as_of_ts",
    "bars_available",
    "close_at_entry",
    "rsi_bucket",
    "atr_pct",
    "forward_bucket",
    "relative_volume",
    "rvol_bucket",
    "gap_pct",
    "trend_pct",
    "above_slow_sma",
    "market_regime",
    "nifty_trend_pct",
    "benchmark_symbol",
    #: Where the entry sat relative to the session VWAP. Present as a column so
    #: the shape is stable and a client can render it, and always ``None``: the
    #: price cache holds daily bars and a session VWAP cannot be recovered from
    #: one. See :data:`forward.research.learning_axes.requested_but_unavailable`.
    # portfolio context at entry
    "portfolio_exposure_at_entry",
    "strategy_allocation",
    "sector_exposure_pct",
    "position_concentration",
    # sizing & risk budgeting context at entry
    "risk_per_trade",
    "position_size",
    "position_value",
    "stop_distance",
    "forward_based_risk",
    # post-trade attribution — journal rows only
    #: The nine-branch attribution's sliceable fields, joined from
    #: ``trade_attributions`` by ``trade_id``. Present for a journal trade that
    #: has been attributed; ``None`` for a backtest row, for a paper-ledger row,
    #: and for a journal trade the sweep has not reached yet. A ``None`` here is
    #: **not evidence that execution was clean** — the availability of the
    #: feature is itself reported through ``missing_features``, so a reader can
    #: tell "attributed and unremarkable" from "never attributed".
    "attribution_entry_quality",
    "attribution_execution_quality",
    "attribution_exit_quality",
    "attribution_entry_slippage_bps",
    "attribution_exit_slippage_bps",
    "attribution_total_slippage_bps",
    "attribution_transaction_costs",
    "attribution_cost_pct",
    "attribution_signal_to_order_sec",
    "attribution_order_to_fill_sec",
    "attribution_holding_sec",
    "attribution_partial_fill",
    "attribution_fill_ratio",
    "attribution_sizing_method",
    "attribution_sizing_cap_reason",
    "attribution_realized_risk_pct",
    "attribution_planned_risk_amount",
    "attribution_mfe_over_risk",
    "attribution_realized_over_risk",
    "attribution_capture_efficiency_pct",
    "attribution_context_score",
    "attribution_context_class",
    "attribution_sector_strength",
    "attribution_stock_relative_strength",
    "attribution_rvol",
    "attribution_atr_pct",
    "attribution_market_regime",
    #: The codes, comma-joined, exactly as the attribution row stores them. Kept
    #: as one column rather than exploded into a boolean per code: a code is a
    #: description of a trade, not an axis, and the axes that read attribution
    #: read the fields above.
    "attribution_reason_codes",
    # bookkeeping
    "missing_features",
)


@dataclass
class LearningDataset:
    """One normalised table of every backtest, paper and live trade.

    Not a "collection of trades" with a frame attached — the frame *is* the

    dataset, and every accessor works off it. That keeps a single representation

    of the data, so the performance analysis, the drift analysis and the report

    cannot disagree about what happened.

    """

    rows: list[dict[str, Any]]
    missing_features: dict[str, str]
    generated_at: datetime
    #: Non-None when the cache could not be read, so a caller can tell "no
    #: features" from "no price data" without inspecting every row.
    warnings: list[str] = field(default_factory=list)
    #: Per-source row counts before any filtering.
    source_counts: dict[str, int] = field(default_factory=dict)
    #: The paper ledger's own account of what it holds — how many weeks, how many
    #: of them forward, and whether the forward count is zero. Carried on the
    #: dataset rather than left to the caller to recompute, because "the record
    #: exists and holds no forward week" is a finding about the *evidence*, not
    #: about the trades, and it is the one a reader most needs.
    ledger: dict[str, Any] = field(default_factory=dict)

    def __len__(self) -> int:
        """Return the number of rows in the dataset."""
        return len(self.rows)

    @property
    def empty(self) -> bool:
        """Whether the dataset has no rows."""
        return not self.rows

    def frame(self):
        """Return rows as a DataFrame with a stable set of columns.

        An empty dataset still has every column, so callers can use the same
        DataFrame operations for empty and non-empty data.

        Returns:
            A DataFrame with every column in :data:`DATASET_COLUMNS`.

        """
        import pandas as pd

        if not self.rows:
            return pd.DataFrame({name: pd.Series(dtype="object") for name in DATASET_COLUMNS})
        frame = pd.DataFrame(self.rows)
        for name in DATASET_COLUMNS:
            if name not in frame.columns:
                frame[name] = None
        return frame[list(DATASET_COLUMNS)]

    def by_source(self, source: str) -> list[dict[str, Any]]:
        """Return rows from one evidence source.

        Args:
            source: Evidence source to keep.

        Returns:
            Rows whose source matches ``source``.
        """
        return [row for row in self.rows if row.get("source") == source]

    def by_class(self, evidence_class: str) -> list[dict[str, Any]]:
        """Return rows from one evidence class.

        Args:
            evidence_class: Evidence class to keep.

        Returns:
            Rows whose class matches ``evidence_class``.
        """
        return [row for row in self.rows if row.get("evidence_class") == evidence_class]

    def by_grade(self, grade: str) -> list[dict[str, Any]]:
        """Return rows with one evidence grade.

        Args:
            grade: Evidence grade to keep.

        Returns:
            Rows whose normalized grade matches ``grade``.
        """
        return [row for row in self.rows if row_grade(row) == grade]

    def forward_rows(self) -> list[dict[str, Any]]:
        """Return rows recorded before their outcomes were known.

        Returns:
            Rows with a forward evidence grade.
        """
        return [row for row in self.rows if is_forward_grade(row_grade(row))]

    def in_sample_rows(self) -> list[dict[str, Any]]:
        """Return rows that are measurements rather than forward evidence.

        Returns:
            Rows without a forward evidence grade.
        """
        return [row for row in self.rows if not is_forward_grade(row_grade(row))]

    def class_counts(self) -> dict[str, int]:
        """Count rows by their evidence class.

        Returns:
            Counts for every known evidence class, including zero counts.
        """
        out = {name: 0 for name in EVIDENCE_CLASSES}
        for row in self.rows:
            name = row.get("evidence_class")
            if name in out:
                out[name] += 1
        return out

    def grade_counts(self) -> dict[str, int]:
        """Count rows by evidence grade.

        Returns:
            Counts for every known grade, including zero counts.
        """
        out = {name: 0 for name in GRADES}
        for row in self.rows:
            out[row_grade(row)] += 1
        return out

    def latest_forward_ts(self) -> datetime | None:
        """Return the latest timestamp among forward evidence rows.

        Returns:
            The latest forward trade timestamp, or ``None`` when there is none.

        """
        stamps = [
            row.get("exit_ts") or row.get("entry_ts")
            for row in self.forward_rows()
            if (row.get("exit_ts") or row.get("entry_ts")) is not None
        ]
        return max(stamps) if stamps else None

    def metric_coverage(self) -> dict[str, int]:
        """Count closed rows that contain each outcome metric.

        Returns:
            A count for each outcome metric among closed rows.

        """
        closed = [row for row in self.rows if row.get("exit_ts") is not None]
        return {
            metric: sum(1 for row in closed if row.get(metric) is not None)
            for metric in OUTCOME_METRICS
        }

    def strategies(self) -> list[str]:
        """Return the distinct strategy names in the dataset.

        Returns:
            Strategy keys in sorted order.
        """
        seen = {row.get("strategy_key") for row in self.rows}
        return sorted(name for name in seen if name)

    @property
    def missing_feature_reasons(self) -> dict[str, str]:
        """Return missing-feature reasons for features absent from any row.

        Returns:
            Reasons keyed by feature name.
        """
        counts: dict[str, int] = {}
        for row in self.rows:
            for feature in row.get("missing_features") or []:
                counts[feature] = counts.get(feature, 0) + 1
        merged: dict[str, str] = {}
        for feature in counts:
            merged[feature] = self.missing_features.get(
                feature, f"not available for {counts[feature]} of {len(self.rows)} trades"
            )
        return merged

    def summary(self) -> dict[str, Any]:
        """Summarize the evidence and coverage in this dataset.

        The figures the learning screen leads with are all here, because a

        client that has to derive them will eventually derive one of them wrong:

        total observations, forward observations, in-sample observations, the

        latest forward trade, the metric coverage, and the evidence breakdown —

        by grade (the direction, which is what a finding depends on) and by class

        (the precise provenance, which is what the drift comparison needs).

        Returns:
            Counts, dates, P&L coverage, and missing-feature details.
        """
        closed = [row for row in self.rows if row.get("exit_ts") is not None]
        pnl = [row.get("net_pnl") for row in closed if row.get("net_pnl") is not None]
        grades = self.grade_counts()
        return {
            "generated_at": _iso(self.generated_at),
            "observations": len(self.rows),
            "trades": len(self.rows),
            "closed_trades": len(closed),
            "open_trades": len(self.rows) - len(closed),
            "forward_observations": grades[GRADE_FORWARD],
            "in_sample_observations": grades[GRADE_IN_SAMPLE],
            "latest_forward_ts": _iso(self.latest_forward_ts()),
            "sources": {source: self.source_counts.get(source, 0) for source in SOURCES},
            #: ``{forward, in_sample}`` — always both keys, so "0 forward" is
            #: stated rather than inferred from an absent field.
            "evidence_grades": grades,
            "evidence_classes": self.class_counts(),
            "metric_coverage": self.metric_coverage(),
            #: How many rows retain a recorded context score. Coverage, not a
            #: finding: a row without one contributes no band anywhere.
            "context_coverage": {
                "with_score": sum(1 for row in self.rows if row.get("context_score") is not None),
                "rows": len(self.rows),
            },
            "paper_ledger": dict(self.ledger),
            "strategies": self.strategies(),
            "symbols": len({row.get("symbol") for row in self.rows if row.get("symbol")}),
            "date_range": _date_range(closed),
            "net_pnl_total": round(sum(pnl), 2) if pnl else None,
            "missing_features": dict(self.missing_features),
            "warnings": list(self.warnings),
        }


def _date_range(rows: list[dict[str, Any]]) -> dict[str, str | None]:
    """Return the first and last entry timestamps in the rows.

    Args:
        rows: Dataset rows to summarize.

    Returns:
        First and last timestamps in ISO form, or ``None`` values when empty.
    """
    stamps = [row.get("entry_ts") for row in rows if row.get("entry_ts") is not None]
    if not stamps:
        return {"first": None, "last": None}
    return {"first": _iso(min(stamps)), "last": _iso(max(stamps))}


def _same_day(when: Any, day: Any) -> bool:
    """Check whether a timestamp belongs to the given date.

    Args:
        when: Timestamp or date to compare.
        day: Date expected for the timestamp.

    Returns:
        Whether ``when`` has the same date as ``day``.
    """
    if when is None:
        return False
    stamp = when.date() if hasattr(when, "date") else when
    return stamp == day


def _round(value: float | None, digits: int = 2) -> float | None:
    """Round a present numeric value, preserving a missing value.

    Args:
        value: Value to round.
        digits: Number of decimal places to keep.

    Returns:
        Rounded value, or ``None`` when the input is missing.
    """
    if value is None:
        return None
    return round(float(value), digits)


def _iso(value: Any) -> str | None:
    """Format a date-like value as text without inventing a missing value.

    Args:
        value: Value that may provide ``isoformat``.

    Returns:
        ISO text, the string form of another value, or ``None``.
    """
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)
