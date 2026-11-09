"""Learning evidence analysis and outcome metrics."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from forward.core.learning_evidence import has_grade, is_forward_grade, row_grade
from forward.research import learning_stats as stats
from forward.research.learning_axes import AXES_BY_NAME, Axis, axes_from_names, axis_coverage
from forward.services.learning_dataset_model import OUTCOME_METRICS, LearningDataset


@dataclass
class AxisBreakdown:
    """Every bucket of one axis, with the baseline they were carved from."""

    axis: str
    label: str
    metric: str
    rows_with_value: int
    rows_scanned: int
    #: The baseline: all *other* rows, used for the lift comparison.
    baseline: dict[str, Any]
    buckets: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "axis": self.axis,
            "label": self.label,
            "metric": self.metric,
            "rows_with_value": self.rows_with_value,
            "rows_scanned": self.rows_scanned,
            "coverage": (
                round(self.rows_with_value / self.rows_scanned, 4) if self.rows_scanned else None
            ),
            "baseline": self.baseline,
            "buckets": self.buckets,
        }


@dataclass
class StrategyAnalysis:
    strategy: str
    n: int
    overall: dict[str, Any]
    breakdowns: list[AxisBreakdown]
    #: Buckets that survived the sample floor and the significance filter,
    #: strongest first. This is the part a report quotes.
    notable: list[dict[str, Any]]
    #: Which outcome column every number above was computed from. Carried on the
    #: result rather than left to the caller to remember: a table of means with
    #: the unit unstated is a table that gets misread.
    metric: str = "net_pnl"
    #: Set when the metric was resolved rather than requested, so a reader is
    #: told that the unit is not the one they might have assumed.
    metric_note: str | None = None
    caveats: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "n": self.n,
            "metric": self.metric,
            "metric_note": self.metric_note,
            "overall": self.overall,
            "breakdowns": [b.as_dict() for b in self.breakdowns],
            "notable": list(self.notable),
            "caveats": list(self.caveats),
        }


class PerformanceAnalysis:
    """Slices a strategy's trades along the declared axes.

    The metric defaults to the outcome column the book actually records. It
    prefers ``net_pnl`` — the actual rupees — because expectancy in currency is
    what a decision is made on, and falls back to ``return_pct`` when no trade
    carries a currency amount, naming the substitution in ``metric_note``. The
    alternative, silently reporting an empty table for a book that is full of
    trades, is the "unknown reported as a zero" failure wearing a suit.
    """

    def __init__(self, dataset: LearningDataset, *, metric: str | None = None) -> None:
        resolved, note = resolve_metric(dataset, metric)
        self.dataset = dataset
        self.metric = resolved
        #: Set when the metric was chosen rather than asked for. Always surfaced
        #: in the payload, because a table of means whose unit changed without
        #: saying so is a table that gets misread.
        self.metric_note = note

    # ------------------------------------------------------------------
    def analyse(
        self,
        strategy: str | None = None,
        *,
        roles: Iterable[str] | None = None,
        classes: Iterable[str] | None = None,
        grades: Iterable[str] | None = None,
        axes: list[str] | None = None,
        min_sample: int = stats.MIN_SAMPLE,
    ) -> StrategyAnalysis:
        """One strategy, or the whole book when ``strategy`` is None.

        ``classes`` restricts the sample by ``evidence_class`` — the precise
        provenance. ``grades`` restricts it by ``evidence_grade`` — the
        direction. Asking for ``grades=["forward"]`` is how a caller gets a
        clean out-of-sample read without having to enumerate the classes, and it
        is the filter a finding should be published on.

        Both are offered rather than one because they answer different
        questions: ``grades`` asks "is this evidence?", ``classes`` asks "was
        this paper or real money?".
        """
        rows = self._rows_for(strategy, roles, classes, grades)
        resolved = axes_from_names(axes)
        values = self._metric_values(rows)
        overall = stats.summarise(values).as_dict()
        caveats = self._caveats(rows, values)
        if self.metric_note:
            caveats.insert(0, self.metric_note)
        breakdowns: list[AxisBreakdown] = []
        notable: list[dict[str, Any]] = []
        for axis in resolved:
            breakdown = self._breakdown(axis, rows, min_sample=min_sample)
            breakdowns.append(breakdown)
            # The axis is carried onto each notable entry rather than left to be
            # inferred from the breakdown list. The notable set is a flattened,
            # re-sorted union of every axis, so without this a reader cannot tell
            # which axis a finding belongs to — and "normal" is a bucket of both
            # the ATR axis and the RVOL axis.
            notable.extend(
                {**bucket, "axis": breakdown.axis}
                for bucket in breakdown.buckets
                if not bucket.get("suppressed")
                and bucket.get("significance") in {"strong", "moderate", "weak"}
            )
        notable.sort(
            key=lambda bucket: (
                {"strong": 0, "moderate": 1, "weak": 2}.get(bucket.get("significance"), 3),
                -(bucket.get("n") or 0),
            )
        )
        return StrategyAnalysis(
            strategy=strategy or "ALL",
            n=len(values),
            overall=overall,
            breakdowns=breakdowns,
            notable=notable,
            metric=self.metric,
            metric_note=self.metric_note,
            caveats=caveats,
        )

    # ------------------------------------------------------------------
    def _rows_for(
        self,
        strategy: str | None,
        roles: Iterable[str] | None,
        classes: Iterable[str] | None = None,
        grades: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        rows = self.dataset.rows
        if strategy:
            rows = [row for row in rows if matches_strategy(row, strategy)]
        if roles:
            allowed = {str(role).upper() for role in roles}
            rows = [row for row in rows if str(row.get("source") or "").upper() in allowed]
        if classes:
            wanted = {str(name).strip().upper() for name in classes}
            rows = [row for row in rows if str(row.get("evidence_class") or "").upper() in wanted]
        if grades:
            # Resolved through ``row_grade`` rather than compared to the stored
            # column, so a row that carries only a class or only the boolean
            # still filters correctly instead of silently dropping out of a
            # forward-only sample.
            wanted_grades = {str(name).strip().lower() for name in grades}
            rows = [row for row in rows if row_grade(row) in wanted_grades]
        return [row for row in rows if row.get("exit_ts") is not None]

    def _metric_values(self, rows: list[dict[str, Any]]) -> list[float]:
        out = []
        for row in rows:
            value = row.get(self.metric)
            if value is None:
                continue
            try:
                numeric_value = float(value)
            except TypeError, ValueError:
                continue
            if math.isfinite(numeric_value):
                out.append(numeric_value)
        return out

    def _breakdown(
        self, axis: Axis, rows: list[dict[str, Any]], *, min_sample: int
    ) -> AxisBreakdown:
        """Every bucket of one axis, each compared to the rest of the sample.

        The comparison count is the axis's **declared vocabulary size**, not the
        number of buckets that happened to be non-empty. Using the observed count
        would let a small sample shrink its own multiple-comparisons penalty,
        which is precisely backwards.
        """
        have, scanned = axis_coverage(axis, rows)
        by_bucket: dict[str, list[float]] = {}
        by_bucket_times: dict[str, list[Any]] = {}
        for row in rows:
            label = axis.value(row)
            if label is None:
                continue
            value = row.get(self.metric)
            if value is None:
                continue
            try:
                numeric_value = float(value)
            except TypeError, ValueError:
                continue
            if not math.isfinite(numeric_value):
                continue
            by_bucket.setdefault(str(label), []).append(numeric_value)
            by_bucket_times.setdefault(str(label), []).append(row.get("entry_ts"))
        comparisons = axis.max_values or max(1, len(by_bucket))
        bucket_labels = sorted(by_bucket)
        verdicts: list[dict[str, Any]] = []
        for label in bucket_labels:
            others = [
                value for other, values in by_bucket.items() if other != label for value in values
            ]
            others_times = [
                stamp
                for other, stamps in by_bucket_times.items()
                if other != label
                for stamp in stamps
            ]
            verdict = stats.compare_bucket(
                label,
                by_bucket[label],
                others,
                comparisons=comparisons,
                min_sample=min_sample,
                bucket_times=by_bucket_times[label],
                baseline_times=others_times,
            )
            verdicts.append(verdict.as_dict())
        return AxisBreakdown(
            axis=axis.name,
            label=axis.label,
            metric=self.metric,
            rows_with_value=have,
            rows_scanned=scanned,
            baseline=stats.summarise(self._metric_values(rows)).as_dict(),
            buckets=verdicts,
        )

    def _caveats(self, rows: list[dict[str, Any]], values: list[float]) -> list[str]:
        out: list[str] = []
        if not rows:
            out.append(
                "no closed trades matched, so every statistic below is absent rather than zero"
            )
            return out
        if not values:
            # Rows exist but none carries the metric. That is a different
            # statement from "no trades", and reporting it as a small sample
            # would send a reader looking for more trades when the fix is to
            # pick the metric the book actually records.
            out.append(
                f"{len(rows)} closed trades matched but not one carries "
                f"{self.metric}, so every statistic below is absent rather than "
                "zero — check the dataset's metric_coverage for the column this "
                "book does record"
            )
            return out
        if len(values) < stats.MIN_SAMPLE:
            out.append(
                f"{len(values)} closed trades is below the {stats.MIN_SAMPLE}-trade "
                "floor; no bucket is analysed and the headline is not evidence"
            )
        elif len(values) < stats.SMALL_SAMPLE:
            out.append(
                f"{len(values)} closed trades is a small sample; every interval "
                "is wide and the notable list should be read as a prompt to keep "
                "collecting data"
            )
        forward = sum(1 for row in rows if is_forward_grade(row_grade(row)))
        ungraded = sum(1 for row in rows if not has_grade(row))
        if ungraded:
            # An unclassified row is not an in-sample row. Saying it were would be
            # the same class of error as reporting a missing value as a zero,
            # in the one column that decides how much a finding is worth.
            out.append(
                f"{ungraded} of {len(rows)} trades carry no evidence class, so "
                "how independent this sample is cannot be stated"
            )
        elif forward == 0:
            out.append(
                "no trade in this sample is forward evidence: every row is "
                "graded in-sample, so these buckets describe the history the "
                "rules were selected on rather than whether they still work"
            )
        elif forward < len(rows):
            out.append(
                f"{len(rows) - forward} of {len(rows)} trades are graded "
                "in-sample and the rest forward; a bucket that mixes the two is "
                "not a clean out-of-sample result, so read the mix before the "
                "bucket"
            )
        best = max(values) if values else None
        total = sum(values)
        if self.metric == "return_pct":
            # ``stats.summarise`` reports ``total`` as the arithmetic sum, which
            # is right for rupees and meaningless for returns: summing 140
            # equal-weight pick returns reports the basket 140 times. The mean is
            # the figure that means something, so the misread is named rather
            # than left to the reader's intuition about the column's name.
            out.append(
                f"figures are in return_pct, so 'total' is the arithmetic sum of "
                f"{len(values)} per-trade returns and is not a portfolio return; "
                f"the equal-weight figure is the mean, {sum(values) / len(values):.2f}%"
            )
        if best is not None and total > 0 and best > total * 0.5:
            out.append(
                f"the single best trade accounts for {best / total:.0%} of the total; "
                "the aggregate is one outcome, not a distribution"
            )
        return out


def coverage_phrase(coverage: dict[str, int]) -> str:
    """``"net_pnl 0, return_pct 140"`` — the coverage, said compactly."""
    return ", ".join(f"{metric} {coverage.get(metric, 0)}" for metric in OUTCOME_METRICS)


def bucket_source_trade_refs(
    rows: list[dict[str, Any]], axis_name: str, label: str, metric: str
) -> list[str]:
    """Return source references for rows counted in one analysis bucket.

    Args:
        rows: Dataset rows supplied to the performance analysis.
        axis_name: Declared analysis axis name.
        label: Bucket label returned by the analysis.
        metric: Outcome column used by the analysis.

    Returns:
        Source trade references for matching rows with a numeric outcome.
    """
    axis = AXES_BY_NAME[axis_name]
    refs = []
    for row in rows:
        value = row.get(metric)
        axis_value = axis.value(row)
        if axis_value is None or str(axis_value) != label or value is None:
            continue
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(numeric_value):
            continue
        ref = row.get("trade_ref") or row.get("trade_id")
        if ref:
            refs.append(str(ref))
    return refs



def resolve_metric(
    dataset: LearningDataset, requested: str | None = None
) -> tuple[str, str | None]:
    """``(metric, note)`` — the outcome column to rank on, and why if it changed.

    An outcome column can be **absent** rather than empty. The paper ledger
    records an equal-weight return and no position size, so a book built from it
    has ``return_pct`` on every row and ``net_pnl`` on none. Ranking buckets on
    the absent column produces a correctly-shaped table of nothing, which reads
    exactly like a book that broke even.

    So: an explicit request is honoured, and the caveat machinery says when it
    has no coverage. With no request, the metric that has coverage is chosen and
    the substitution is named. Both paths say what happened; neither invents a
    number.
    """
    coverage = dataset.metric_coverage()
    if requested is not None:
        # Honoured as asked. ``PerformanceAnalysis._caveats`` reports the zero
        # coverage, so the note would only repeat it.
        return requested, None
    preferred = OUTCOME_METRICS[0]
    if coverage.get(preferred, 0) > 0:
        return preferred, None
    best = max(OUTCOME_METRICS, key=lambda metric: coverage.get(metric, 0))
    if coverage.get(best, 0) > 0:
        return best, (
            f"{preferred} has no coverage in this dataset, so the analysis ran on "
            f"{best} ({coverage_phrase(coverage)})"
        )
    # Nothing carries either. Keep the preferred name so the output is
    # deterministic, and let the empty-sample caveat explain.
    return preferred, None


def matches_strategy(row: dict[str, Any], strategy: str) -> bool:
    """Match on any of the three names a strategy can be known by.

    A built-in is identified by ``engine_key``; a saved one by ``strategy_id``.
    A caller passing either spelling, or an ``id@version`` pin, should find its
    trades rather than an empty result that looks like "it made nothing".
    """
    target = str(strategy).strip()
    if "@" in target:
        name, _, version = target.partition("@")
        if str(row.get("strategy_version")) != version.strip():
            return False
        target = name.strip()
    candidates = {
        str(row.get("strategy_key") or ""),
        str(row.get("strategy_id") or ""),
        str(row.get("engine_key") or ""),
    }
    return target in candidates
