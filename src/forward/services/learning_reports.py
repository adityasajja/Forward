"""Daily learning reports built from recorded trade evidence."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from forward.config.settings import get_settings
from forward.core.learning_evidence import (
    GRADE_FORWARD,
    GRADES,
    SOURCES,
    is_forward_grade,
    row_grade,
)
from forward.research import learning_stats as stats
from forward.research.learning_drift import analyse as drift_analysis
from forward.services.learning_analysis import (
    PerformanceAnalysis,
    resolve_metric,
)
from forward.services.learning_dataset_model import LearningDataset, _round, _same_day
from forward.services.learning_report_model import DailyReport as _DailyReport
from forward.services.learning_report_model import format_amount


def _count_labels(labels: Iterable[str]) -> dict[str, int]:
    """Count labels in the order they first appear.

    Args:
        labels: Labels collected from the report's source rows.

    Returns:
        Counts keyed by label, in first-seen order.
    """
    counts: dict[str, int] = {}
    for label in labels:
        counts[label] = counts.get(label, 0) + 1
    return counts


class DailyLearningReport:
    """End-of-day narrative. Advisory, and structured so it cannot be anything else.

    The ``advisory`` flag and ``applies_changes: False`` are in the payload, not
    only in a docstring, because the payload is what a UI renders and what a
    future caller would branch on. A report that returns a list of parameter
    values would sooner or later be fed to something that applies them.

    Two jobs, and they have different evidence requirements
    ------------------------------------------------------

    **Say what happened.** A day's trades, their aggregate, and its arithmetic
    against the preceding window. This is a record, and it is produced for the
    whole book — including in-sample rows — because a book of in-sample trades
    still had a day and withholding it would leave the reader to assume nothing
    occurred.

    **Say whether it was good.** This is a claim, and it is produced only when
    the book holds enough *forward* observations. The gate is on
    :attr:`DailyReport.claimable`, and it counts forward observations
    specifically: a thousand in-sample rows do not make a claim supportable,
    because they were measured on the history the rules were chosen from.

    Conflating the two is the failure this class is shaped to prevent. A report
    that gated the *facts* would go silent on a real trading day; a report that
    ungated the *claim* would publish a restatement of the selection history as
    a result.
    """

    def __init__(
        self,
        dataset: LearningDataset,
        *,
        metric: str | None = None,
        grades: Iterable[str] | None = None,
        classes: Iterable[str] | None = None,
    ) -> None:
        resolved, note = resolve_metric(dataset, metric)
        self.dataset = dataset
        self.metric = resolved
        self.metric_note = note
        #: A currency metric aggregates by summing — rupees add up. A percentage
        #: metric aggregates by the equal-weight mean, because summing the
        #: returns of ten equal-weight picks reports ten times the basket. The
        #: two rules are not interchangeable and the report states which it used.
        self.aggregate = "mean" if resolved == "return_pct" else "sum"
        #: The evidence grades this report may draw on.
        #:
        #: Defaults to **both**. The report's first job is to say what happened,
        #: and a book of in-sample rows still produced a day of trading; what is
        #: restricted is the claim, which :attr:`DailyReport.claimable` gates on
        #: the forward count. Narrowing this to ``["forward"]`` is available for
        #: a caller who wants the forward book in isolation, and the payload
        #: always names the scope so the narrowing cannot be invisible.
        self.grades: tuple[str, ...] = (
            tuple(str(name).strip().lower() for name in grades) if grades else GRADES
        )
        #: ``evidence_class`` values, for a caller that wants to narrow by
        #: provenance rather than by direction — "paper only", or "real money
        #: only". Applied on top of the grade filter, so the two compose.
        self.classes: tuple[str, ...] = (
            tuple(str(name).strip().upper() for name in classes) if classes else ()
        )

    def _in_scope(self, row: dict[str, Any]) -> bool:
        """Whether a row belongs in this report's sample.

        Two independent filters: the grade is the *direction* (is this evidence?)
        and the class is the *provenance* (was it paper or real money?). A caller
        wanting paper-only forward evidence sets both; the default sets neither
        and takes the whole book.
        """
        if row_grade(row) not in self.grades:
            return False
        if not self.classes:
            return True
        return str(row.get("evidence_class") or "").upper() in self.classes

    def _aggregate(self, rows: list[dict[str, Any]]) -> float | None:
        values = [float(row[self.metric]) for row in rows if row.get(self.metric) is not None]
        if not values:
            return None
        total = sum(values) if self.aggregate == "sum" else sum(values) / len(values)
        return round(total, 2)

    def build(
        self,
        *,
        as_of: datetime | None = None,
        window_days: int = get_settings().learning_forward_window_days,
    ) -> _DailyReport:
        as_of = as_of or datetime.now(UTC)
        day = as_of.date()
        limitations: list[str] = []
        in_scope = [row for row in self.dataset.rows if self._in_scope(row)]
        book = [row for row in in_scope if row.get("exit_ts") is not None]
        #: The book that can carry a claim. Counted separately from ``book``
        #: because the two answer different questions — how much happened, and
        #: how much of it is evidence — and only the second may gate a finding.
        forward_book = [row for row in book if is_forward_grade(row_grade(row))]
        claimable = len(forward_book) >= stats.MIN_SAMPLE
        today = [row for row in in_scope if _same_day(row.get("exit_ts"), day)]
        today_grades = {name: 0 for name in GRADES}
        for row in today:
            today_grades[row_grade(row)] += 1
        #: The forward subset of today — the **evidence surface**, as opposed to
        #: the fact surface above. The advisory findings are computed from this
        #: and nothing else.
        #:
        #: The distinction is load-bearing. A book with twelve forward rows from
        #: earlier weeks is claimable; if the findings were then computed over a
        #: day whose trades were all backfills, the report would publish a
        #: restatement of the selection history as a result — and it would look
        #: exactly like a finding, because the book that licensed it was real.
        today_forward = [row for row in today if is_forward_grade(row_grade(row))]
        scope = ", ".join(self.grades)
        limitations.append(f"scope: evidence grades {scope}")
        if not book:
            limitations.append(
                "no observation has been recorded, so there is nothing to report "
                "on; an in-sample row is not a substitute for a forward one, "
                "because it was measured on the history the rule was selected from"
            )
        elif not forward_book:
            limitations.append(
                f"all {len(book)} closed observation(s) in the book are graded "
                "in-sample: they are measurements on the history the rules were "
                "selected from, so this report records what happened and makes "
                "no claim about whether anything still works"
            )
        elif not claimable:
            limitations.append(
                f"{len(forward_book)} forward observation(s) is below the "
                f"{stats.MIN_SAMPLE}-observation floor, so nothing here is "
                "claimable yet — the figures are recorded, not evidence"
            )
        if today and not today_forward:
            limitations.append(
                "every trade that closed today is graded in-sample, so today's "
                "figure describes the selection history rather than a forward "
                "test, and no finding is drawn from it"
            )
        elif today_forward and len(today_forward) < len(today):
            limitations.append(
                f"{len(today_forward)} of today's {len(today)} trades are forward; "
                "the findings below are computed from those and not from the "
                "in-sample remainder"
            )
        if self.metric_note:
            limitations.append(self.metric_note)
        if self.aggregate == "mean" and today:
            limitations.append(
                f"figures are in {self.metric} and the day's total is the "
                "equal-weight mean of its trades, not their sum: summing the "
                "returns of an equal-weight basket reports the basket once per "
                "constituent"
            )
        metric_today = self._aggregate(today)
        if not today:
            limitations.append("no trade closed today; today's performance is unmeasured, not zero")
        elif metric_today is None:
            limitations.append(
                f"{len(today)} trades closed today and not one carries "
                f"{self.metric}, so today's result is unmeasured rather than flat"
            )
        history = self._history_rows(day, window_days)
        expectation = self._expectation(history, window_days, limitations)
        deviation = self._deviation(metric_today, expectation, limitations)
        by_strategy = self._by_strategy(today)
        # The inputs to a finding, and therefore drawn from the evidence surface
        # rather than from the day. ``regime`` and ``unusual`` stay on the whole
        # day because they are counts, and a count is a fact about the day
        # whichever grade its rows carry.
        strongest, weakest = self._best_and_worst(today_forward)
        regime = self._regime(today, limitations)
        unusual = self._unusual(today, history)
        execution = self._execution(today, limitations)
        drift = self._drift_placeholder()
        # Advisory findings are a claim, not a fact, so they need three things:
        # enough forward observations in the book, at least one forward
        # observation *today* to have a finding about, and the findings
        # themselves computed from the forward rows alone. A recommendation
        # drawn from six forward trades is the overfitting the whole engine
        # exists to refuse; a recommendation drawn from six hundred in-sample
        # rows is the same mistake wearing a bigger number; and a recommendation
        # drawn from a backfilled day because the book was licensed elsewhere is
        # the same mistake wearing a disguise.
        observations = (
            self._observations(today_forward, history, expectation, strongest, weakest, limitations)
            if claimable and today_forward
            else []
        )
        return _DailyReport(
            as_of=day.isoformat(),
            window_days=window_days,
            trades_today=len(today),
            metric_total=metric_today,
            expectation=expectation,
            deviation=deviation,
            by_strategy=by_strategy,
            strongest=strongest,
            weakest=weakest,
            regime=regime,
            unusual=unusual,
            execution=execution,
            drift=drift,
            observations=observations,
            limitations=limitations,
            metric=self.metric,
            metric_note=self.metric_note,
            aggregate=self.aggregate,
            scope=list(self.grades),
            forward_observations=len(forward_book),
            in_sample_observations=len(book) - len(forward_book),
            today_grades=today_grades,
            claimable=claimable,
        )

    # ------------------------------------------------------------------
    def _history_rows(self, day, window_days: int) -> list[dict[str, Any]]:
        """Closed trades in the window *before* today.

        Excludes today deliberately. A baseline that includes the day being
        judged is partly a comparison of the day with itself, which shrinks the
        reported deviation toward zero — the flattering direction.
        """
        cutoff = day - timedelta(days=window_days)
        out = []
        for row in self.dataset.rows:
            if not self._in_scope(row):
                continue
            exit_ts = row.get("exit_ts")
            if exit_ts is None:
                continue
            stamp = exit_ts.date() if hasattr(exit_ts, "date") else exit_ts
            if cutoff <= stamp < day:
                out.append(row)
        return out

    def _expectation(
        self, history: list[dict[str, Any]], window_days: int, limitations: list[str]
    ) -> dict[str, Any]:
        values = [float(row[self.metric]) for row in history if row.get(self.metric) is not None]
        if not values:
            limitations.append(
                "no closed trades in the preceding window, so there is no "
                "historical expectation to compare today against"
            )
            return {
                "basis": "none",
                "window_days": window_days,
                "sample_trades": 0,
                "mean_per_day": None,
                "mean_per_trade": None,
                "median_per_day": None,
                "interval_per_day": None,
                "best_day": None,
                "worst_day": None,
                "note": "the expectation is absent rather than assumed to be zero",
            }
        by_day: dict[str, list[float]] = {}
        for row in history:
            exit_ts = row.get("exit_ts")
            if exit_ts is None or row.get(self.metric) is None:
                continue
            key = (exit_ts.date() if hasattr(exit_ts, "date") else exit_ts).isoformat()
            by_day.setdefault(key, []).append(float(row[self.metric]))
        # Per-day aggregates follow the same rule as the day's total: rupees add
        # up across the day's trades, a percentage does not.
        daily = [
            sum(values) if self.aggregate == "sum" else sum(values) / len(values)
            for values in by_day.values()
        ]
        trading_days = len(daily)
        interval = stats.mean_ci(daily)
        return {
            "basis": "per_trading_day",
            "window_days": window_days,
            "sample_trades": len(values),
            "trading_days": trading_days,
            "mean_per_day": round(sum(daily) / trading_days, 2) if trading_days else None,
            "mean_per_trade": round(sum(values) / len(values), 2),
            "median_per_day": _round(stats.median(daily)),
            "interval_per_day": [round(v, 2) for v in interval] if interval else None,
            "best_day": _round(max(daily)) if daily else None,
            "worst_day": _round(min(daily)) if daily else None,
            "note": (
                "the mean is over trading days that had a closed trade, and the "
                "best and worst days are quoted because one day can carry the mean"
            ),
        }

    def _deviation(
        self, pnl_today: float | None, expectation: dict[str, Any], limitations: list[str]
    ) -> dict[str, Any]:
        expected = expectation.get("mean_per_day")
        if pnl_today is None or expected is None:
            return {"delta": None, "relative": None, "within_interval": None}
        delta = pnl_today - expected
        relative = (delta / abs(expected)) if expected else None
        interval = expectation.get("interval_per_day")
        within = None
        if interval:
            within = interval[0] <= pnl_today <= interval[1]
        return {
            "delta": round(delta, 2),
            "relative": round(relative, 4) if relative is not None else None,
            "within_interval": within,
        }

    def _by_strategy(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            key = row.get("strategy_key") or row.get("strategy_id") or "unattributed"
            grouped.setdefault(str(key), []).append(row)
        out = []
        for key, items in sorted(grouped.items()):
            values = [float(row[self.metric]) for row in items if row.get(self.metric) is not None]
            out.append(
                {
                    "strategy": key,
                    "trades": len(items),
                    # Named for the metric rather than for rupees: on a
                    # percentage metric this is a mean return, and calling it
                    # ``net_pnl`` would invite it to be read as money.
                    "metric_total": self._aggregate(items),
                    "wins": sum(1 for value in values if value > 0),
                    **stats.summarise(values).as_dict(),
                }
            )
        return out

    def _best_and_worst(
        self, rows: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """The strongest and weakest *setups* today, by bucket, not by trade.

        Named per-setup rather than per-trade because "the best trade" is not
        actionable — you cannot choose to take only today's winner. A setup with
        three trades and a positive mean is a pattern; a single lucky trade is
        not, and the ``MIN_SAMPLE`` floor keeps the two apart.
        """
        closed = [row for row in rows if row.get("exit_ts") is not None]
        if not closed:
            return ([], [])
        analysis = PerformanceAnalysis(
            LearningDataset(
                rows=closed,
                missing_features={},
                generated_at=self.dataset.generated_at,
            ),
            metric=self.metric,
        )
        result = analysis.analyse(axes=["setup", "rvol_bucket", "market_regime", "exit_reason"])
        notable = [bucket for bucket in result.notable if bucket.get("n", 0) >= stats.MIN_SAMPLE]
        positive = [b for b in notable if (b.get("lift") or 0) > 0]
        negative = [b for b in notable if (b.get("lift") or 0) < 0]
        return (positive[:3], negative[:3])

    def _regime(self, today: list[dict[str, Any]], limitations: list[str]) -> dict[str, Any]:
        labels = [row.get("market_regime") for row in today if row.get("market_regime")]
        if not labels:
            limitations.append(
                "no trade carried a market-regime label, so today's regime is "
                "unknown rather than assumed to be unchanged"
            )
            return {"label": None, "basis": "none", "distribution": {}}
        counts = _count_labels(labels)
        # A tie is reported as a tie, not silently resolved by dict insertion
        # order — which is what ``max(counts, key=counts.get)`` does, and it made
        # the label depend on the order trades happened to be read rather than on
        # the market. ``dominant`` is None on a tie so a caller must decide, and
        # the full distribution is always there to decide from.
        highest = max(counts.values())
        leaders = sorted(name for name, count in counts.items() if count == highest)
        dominant = leaders[0] if len(leaders) == 1 else None
        tied = leaders if len(leaders) > 1 else []
        # The concentration of losing trades in one regime is the observation
        # worth surfacing, and it is a count rather than a claim.
        losers = [
            row for row in today if row.get(self.metric) is not None and float(row[self.metric]) < 0
        ]
        loser_counts: dict[str, int] = {}
        for row in losers:
            label = row.get("market_regime")
            if label:
                loser_counts[label] = loser_counts.get(label, 0) + 1
        return {
            "label": dominant,
            "tied": tied,
            "basis": "entry-time label on today's trades",
            "distribution": counts,
            "losing_trades": len(losers),
            "losing_distribution": loser_counts,
        }

    def _unusual(self, today: list[dict[str, Any]], history: list[dict[str, Any]]) -> list[str]:
        """Facts about today that differ from the window before it.

        Phrased as counts, not as conclusions. "7 of 9 losing trades were in a
        sideways regime" is a fact; "sideways regimes are unprofitable" is a
        claim that needs the bucket statistics behind it, and those are in
        ``strongest``/``weakest``.
        """
        out: list[str] = []
        if not today:
            return out
        today_symbols = {row.get("symbol") for row in today}
        if len(today) >= 5 and len(today_symbols) <= max(1, len(today) // 3):
            out.append(
                f"{len(today)} trades concentrated in {len(today_symbols)} symbols; "
                "the day's result is a bet on a few names rather than the strategy"
            )
        losers = [
            row for row in today if row.get(self.metric) is not None and float(row[self.metric]) < 0
        ]
        regimes = [row.get("market_regime") for row in losers if row.get("market_regime")]
        if len(losers) >= 3 and regimes:
            counts = _count_labels(regimes)
            label, count = max(counts.items(), key=lambda item: item[1])
            if count > len(losers) / 2:
                out.append(f"{count} of {len(losers)} losing trades occurred in a {label} regime")
        holding = [
            float(row["duration_days"]) for row in today if row.get("duration_days") is not None
        ]
        if holding and len(holding) >= 5:
            out.append(f"holding period today ranged {min(holding):.1f} to {max(holding):.1f} days")
        return out

    def _execution(self, today: list[dict[str, Any]], limitations: list[str]) -> dict[str, Any]:
        """Slippage and excursion, from the journal only."""
        slippage = [
            float(row["slippage_bps"]) for row in today if row.get("slippage_bps") is not None
        ]
        mae = [float(row["mae"]) for row in today if row.get("mae") is not None]
        mfe = [float(row["mfe"]) for row in today if row.get("mfe") is not None]
        if not slippage and today:
            limitations.append(
                "no slippage was recorded for today's trades — the backtest "
                "carries it per run, not per trade, and the journal fills it per "
                "trade only when it exists"
            )
        return {
            "slippage_bps_mean": _round(sum(slippage) / len(slippage)) if slippage else None,
            "slippage_bps_max": _round(max(slippage)) if slippage else None,
            "trades_with_slippage": len(slippage),
            "mfe_mean": _round(sum(mfe) / len(mfe)) if mfe else None,
            "mae_mean": _round(sum(mae) / len(mae)) if mae else None,
            "trades_with_excursions": len(mae),
        }

    def _drift_placeholder(self) -> dict[str, Any]:
        """Drift, summarised as a verdict plus a pointer to the full analysis.

        The full metric table is *not* duplicated here. Two copies of the same
        numbers in one payload drift apart the moment either side is tuned, and
        the report is the one a human reads — so it carries the conclusion and
        the sample sizes, and the dashboard renders the detail from
        ``LearningService.drift()``.
        """
        counts = self.dataset.source_counts
        analysis = drift_analysis(self.dataset.rows)
        comparable = [pair for pair in analysis.pairs if pair.status == "ok"]
        return {
            "available": bool(comparable),
            "counts": {source: counts.get(source, 0) for source in SOURCES},
            "headline": analysis.headline,
            "pairs": [
                {
                    "reference": pair.reference,
                    "comparison": pair.comparison,
                    "reference_n": pair.reference_n,
                    "comparison_n": pair.comparison_n,
                    "status": pair.status,
                    "deteriorated": pair.deteriorated,
                    "improved": pair.improved,
                }
                for pair in analysis.pairs
            ],
            "note": (
                "verdict and sample sizes only; call the drift analysis for the "
                "per-metric detail so the two cannot disagree"
            ),
        }

    def _observations(
        self,
        forward_today: list[dict[str, Any]],
        history: list[dict[str, Any]],
        expectation: dict[str, Any],
        strongest: list[dict[str, Any]],
        weakest: list[dict[str, Any]],
        limitations: list[str],
    ) -> list[dict[str, Any]]:
        """Advisory statements, each with the evidence that produced it.

        ``forward_today`` is the forward subset of the day, never the whole day.
        A finding is a claim about what the strategy did, and only a row recorded
        before its outcome can support one — the fact surface (the day's count
        and figure) is deliberately wider, because saying what happened is not a
        claim.

        No observation names a parameter value. That is the line between this
        phase and the recommendation engine, and it is held by returning
        ``evidence`` and never ``proposed_value``.
        """
        out: list[dict[str, Any]] = []
        expected = expectation.get("mean_per_day")
        net = self._aggregate(forward_today)
        if (
            net is not None
            and expected is not None
            and len(forward_today) >= 5
            and net < 0
            and expected > 0
        ):
            out.append(
                {
                    "kind": "expectancy_shortfall",
                    "severity": "review",
                    "statement": (
                        f"today's {self.metric} of {format_amount(net, self.metric)} "
                        "is negative against a positive historical expectation of "
                        f"{format_amount(expected, self.metric)} per day"
                    ),
                    "evidence": {
                        "trades_today": len(forward_today),
                        "metric": self.metric,
                        "aggregate": self.aggregate,
                        "metric_today": _round(net),
                        "expected_per_day": expected,
                        "history_trades": expectation.get("sample_trades"),
                        "evidence_grade": GRADE_FORWARD,
                    },
                    "confidence": "low",
                    "sample_size": len(forward_today),
                    "advisory": True,
                }
            )
        for bucket in weakest[:2]:
            out.append(
                {
                    "kind": "weak_bucket",
                    "severity": "review",
                    "statement": (
                        f"Over {bucket.get('n')} forward trades, condition '{bucket.get('label')}' "
                        f"averaged {bucket['stats'].get('mean')}, "
                        f"{abs(bucket.get('lift') or 0):.2f} below the baseline"
                    ),
                    "evidence": {
                        "axis": bucket.get("axis"),
                        "bucket": bucket.get("label"),
                        "n": bucket.get("n"),
                        "stats": bucket.get("stats"),
                        "lift": bucket.get("lift"),
                        "p_value": bucket.get("p_value"),
                        "significance": bucket.get("significance"),
                    },
                    "confidence": _confidence_word(bucket),
                    "sample_size": bucket.get("n"),
                    "advisory": True,
                }
            )
        for bucket in strongest[:1]:
            out.append(
                {
                    "kind": "strong_bucket",
                    "severity": "note",
                    "statement": (
                        f"Over {bucket.get('n')} forward trades, condition '{bucket.get('label')}' "
                        f"averaged {bucket['stats'].get('mean')}, "
                        f"{bucket.get('lift'):.2f} above the baseline"
                    ),
                    "evidence": {
                        "axis": bucket.get("axis"),
                        "bucket": bucket.get("label"),
                        "n": bucket.get("n"),
                        "stats": bucket.get("stats"),
                        "lift": bucket.get("lift"),
                        "p_value": bucket.get("p_value"),
                        "significance": bucket.get("significance"),
                    },
                    "confidence": _confidence_word(bucket),
                    "sample_size": bucket.get("n"),
                    "advisory": True,
                }
            )
        if history and not forward_today:
            out.append(
                {
                    "kind": "no_activity",
                    "severity": "note",
                    "statement": (
                        "no forward trade closed today while the preceding window "
                        "was active; check the runner's blocked reason before "
                        "reading anything into it"
                    ),
                    "evidence": {"history_trades": len(history)},
                    "confidence": "n/a",
                    "sample_size": 0,
                    "advisory": True,
                }
            )
        return out


def _confidence_word(bucket: dict[str, Any]) -> str:
    label = bucket.get("significance")
    return {
        "strong": "high",
        "moderate": "medium",
        "weak": "low",
    }.get(label, "insufficient")
