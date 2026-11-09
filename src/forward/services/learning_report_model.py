"""Daily learning report model and presentation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from forward.core.learning_evidence import GRADE_FORWARD, GRADES
from forward.services.learning_dataset_model import _round


@dataclass
class DailyReport:
    """One trading day, described against what the history expected.

    Every field that could not be computed is ``None`` with the reason in
    ``limitations``. The report never falls back to a default number, because a
    default is indistinguishable from a measurement once it is printed.
    """

    as_of: str
    window_days: int
    trades_today: int
    #: Today's aggregate **in :attr:`metric` units**. A sum when the metric is a
    #: currency amount, an equal-weight mean when it is a percentage — see
    #: :attr:`aggregate`. The two are not interchangeable: summing the returns of
    #: ten equal-weight picks reports ten times the basket.
    metric_total: float | None
    expectation: dict[str, Any]
    deviation: dict[str, Any]
    by_strategy: list[dict[str, Any]]
    strongest: list[dict[str, Any]]
    weakest: list[dict[str, Any]]
    regime: dict[str, Any]
    unusual: list[str]
    execution: dict[str, Any]
    drift: dict[str, Any]
    observations: list[dict[str, Any]]
    limitations: list[str]
    #: Which outcome column every figure above is expressed in.
    metric: str = "net_pnl"
    #: Set when the metric was resolved rather than requested.
    metric_note: str | None = None
    #: ``"sum"`` for a currency metric, ``"mean"`` for a percentage one.
    aggregate: str = "sum"
    #: The evidence **grades** this report drew on. Always populated, so a
    #: narrowed scope is visible in the payload rather than inferred from the
    #: numbers.
    scope: list[str] = field(default_factory=lambda: list(GRADES))
    #: Closed forward observations in the book, whether or not they closed today.
    forward_observations: int = 0
    #: Closed in-sample observations in the book. Reported beside the forward
    #: count because the two together are what say how much of this book is
    #: evidence — a book of four hundred in-sample rows and two forward ones is
    #: not a well-tested strategy.
    in_sample_observations: int = 0
    #: The grades of the trades that closed **today**, by count. A day whose
    #: trades are all in-sample produced a figure and no evidence, and the two
    #: must be separable on the screen.
    today_grades: dict[str, int] = field(default_factory=dict)
    #: Whether the book is large enough in *forward* observations to support a
    #: claim. Below the sample floor the report states what happened and says
    #: nothing about whether it was good — which is the difference between a
    #: record and a finding. Note that this is deliberately **not** "the book has
    #: enough rows": a thousand in-sample rows support no claim at all.
    claimable: bool = False

    @property
    def net_pnl_today(self) -> float | None:
        """Today's net rupees — and ``None`` when the book records no rupees.

        Deliberately not an alias for :attr:`metric_total`. A book measured in
        returns has no rupee total, and a field named ``net_pnl`` holding a
        percentage is the kind of quietly wrong number this engine exists to
        refuse.
        """
        return self.metric_total if self.metric == "net_pnl" else None

    @property
    def headline(self) -> str:
        """One sentence. Refuses to invent a number it does not have.

        The split this sentence holds is the whole design of the report. **What
        happened is a fact** — a count, a figure and its arithmetic against the
        preceding window — and it is stated whenever it exists, because
        withholding a measurement the book contains would be its own kind of
        dishonesty. **Whether it was good or bad is a claim**, and it is withheld
        until the book holds enough *forward* observations to support one.

        The distinction matters because the alternative is worse than it looks.
        A report that stayed silent about an in-sample day would leave the reader
        to assume nothing happened; a report that called an in-sample day a
        deterioration would be restating the selection history as a test result.
        Stating the figure and naming its grade is the only version that is true
        both ways.
        """
        if self.trades_today == 0:
            base = f"No closed trades on {self.as_of}."
            if self.forward_observations == 0:
                # The state the project is actually in, said rather than
                # implied by an absence of numbers.
                return (
                    f"{base} No forward observation has been recorded yet, so "
                    "there is nothing to report on."
                )
            return base
        if self.metric_total is None:
            return (
                f"{self.trades_today} closed trades on {self.as_of}, but no "
                f"{self.metric} was recorded, so the day's result is unmeasured."
            )
        amount = format_amount(self.metric_total, self.metric)
        grade_note = self._grade_clause()
        expected = self.expectation.get("mean_per_day")
        delta = self.deviation.get("delta")
        if expected is None or delta is None:
            return (
                f"{self.trades_today} closed trades on {self.as_of}, "
                f"{self.metric} {amount}.{grade_note}"
            )
        rel = self.deviation.get("relative")
        direction = "above" if delta >= 0 else "below"
        if rel is None:
            compared = f" — {abs(delta):,.2f} {direction} the historical daily expectation."
        else:
            compared = (
                f" — {abs(rel):.0%} {direction} the historical daily expectation "
                f"of {format_amount(expected, self.metric)}."
            )
        return (
            f"{self.trades_today} closed trades on {self.as_of}, "
            f"{self.metric} {amount}{compared}{grade_note}"
        )

    def _grade_clause(self) -> str:
        """What the day's evidence grade permits the report to say.

        Appended to the headline rather than folded into it, because the figure
        and the verdict have different conditions: the figure is stated whenever
        it exists, the verdict only when the book can carry one.
        """
        forward_today = self.today_grades.get(GRADE_FORWARD, 0)
        if forward_today == 0:
            return (
                " Every trade closing today is graded in-sample, so this "
                "describes the history the rules were selected on and not a "
                "forward test."
            )
        if not self.claimable:
            return (
                f" Too few forward observations ({self.forward_observations}) to "
                "say whether that is good or bad."
            )
        return ""

    def as_dict(self) -> dict[str, Any]:
        """Return the report with values ready for a JSON response.

        Returns:
            The report fields, headline, and evidence limits.
        """
        return {
            "as_of": self.as_of,
            "window_days": self.window_days,
            "headline": self.headline,
            "trades_today": self.trades_today,
            "metric": self.metric,
            "metric_note": self.metric_note,
            "aggregate": self.aggregate,
            "scope": list(self.scope),
            "forward_observations": self.forward_observations,
            "in_sample_observations": self.in_sample_observations,
            "today_grades": dict(self.today_grades),
            "claimable": self.claimable,
            "metric_total": _round(self.metric_total),
            "net_pnl_today": _round(self.net_pnl_today),
            "expectation": self.expectation,
            "deviation": self.deviation,
            "by_strategy": self.by_strategy,
            "strongest": self.strongest,
            "weakest": self.weakest,
            "regime": self.regime,
            "unusual": self.unusual,
            "execution": self.execution,
            "drift": self.drift,
            "observations": self.observations,
            "limitations": self.limitations,
            "advisory": True,
            "applies_changes": False,
        }



def format_amount(value: float, metric: str) -> str:
    """Format a report value with its unit.

    Args:
        value: Value to format.
        metric: Outcome metric that defines the value's unit.

    Returns:
        Signed value with a percent sign for returns, or rupees otherwise.
    """
    if metric == "return_pct":
        return f"{value:+.2f}%"
    return f"{value:+,.2f}"
