"""Result models for learning drift comparisons."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MetricComparison:
    """One metric, measured on two sources.

    ``status`` is the field that matters. It is one of:

    * ``ok``            — both sides cleared the floor and the delta is reported
    * ``insufficient``  — one side is too small; ``reason`` says which
    * ``absent``        — one side has no observations of this metric at all
    * ``not_comparable``— the two sides share nothing to compare

    A caller that only reads ``delta`` will see ``None`` in every non-``ok``
    case, so the honest path is also the default path.
    """

    metric: str
    label: str
    higher_is_better: bool
    reference: str
    comparison: str
    status: str
    reference_value: float | None = None
    comparison_value: float | None = None
    reference_n: int = 0
    comparison_n: int = 0
    delta: float | None = None
    relative: float | None = None
    magnitude: str = "unknown"
    direction: str = "unknown"
    reason: str | None = None
    p_value: float | None = None
    significant: bool = False

    def as_dict(self) -> dict[str, Any]:
        """Return the comparison as a JSON-friendly dictionary."""
        return {
            "metric": self.metric,
            "label": self.label,
            "higher_is_better": self.higher_is_better,
            "reference": self.reference,
            "comparison": self.comparison,
            "status": self.status,
            "reference_value": _round(self.reference_value),
            "comparison_value": _round(self.comparison_value),
            "reference_n": self.reference_n,
            "comparison_n": self.comparison_n,
            "delta": _round(self.delta, 4),
            "relative": _round(self.relative, 4),
            "magnitude": self.magnitude,
            "direction": self.direction,
            "reason": self.reason,
            "p_value": _round(self.p_value, 5),
            "significant": self.significant,
        }


@dataclass
class SourceDrift:
    """Everything one source-vs-source comparison found."""

    reference: str
    comparison: str
    reference_n: int
    comparison_n: int
    status: str
    metrics: list[MetricComparison] = field(default_factory=list)
    distribution: dict[str, Any] = field(default_factory=dict)
    regime: dict[str, Any] = field(default_factory=dict)
    findings: list[dict[str, Any]] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)

    def metric(self, name: str) -> MetricComparison | None:
        """Find a metric by name.

        Args:
            name: Metric name to find.

        Returns:
            The matching metric, or None if it is absent.
        """
        for item in self.metrics:
            if item.metric == name:
                return item
        return None

    @property
    def deteriorated(self) -> list[str]:
        """The metrics that got *worse*, in the direction that matters."""
        return [
            item.metric
            for item in self.metrics
            if item.status == "ok" and item.direction == "deteriorated"
        ]

    @property
    def improved(self) -> list[str]:
        """Return names of metrics that improved."""
        return [
            item.metric
            for item in self.metrics
            if item.status == "ok" and item.direction == "improved"
        ]

    def as_dict(self) -> dict[str, Any]:
        """Return the source comparison as a JSON-friendly dictionary."""
        return {
            "reference": self.reference,
            "comparison": self.comparison,
            "reference_n": self.reference_n,
            "comparison_n": self.comparison_n,
            "status": self.status,
            "metrics": [m.as_dict() for m in self.metrics],
            "distribution": self.distribution,
            "regime": self.regime,
            "findings": self.findings,
            "limitations": self.limitations,
            "deteriorated": self.deteriorated,
            "improved": self.improved,
        }


@dataclass
class DriftAnalysis:
    """The whole picture, across every source pair that can be formed."""

    strategy: str
    available_sources: dict[str, int]
    pairs: list[SourceDrift] = field(default_factory=list)
    headline: str = ""
    findings: list[dict[str, Any]] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    advisory: bool = True
    applies_changes: bool = False

    def pair(self, reference: str, comparison: str) -> SourceDrift | None:
        """Find a comparison by its source names.

        Args:
            reference: Name of the source used as the reference.
            comparison: Name of the source being compared.

        Returns:
            The matching source comparison, or None if it is absent.
        """
        for item in self.pairs:
            if item.reference == reference and item.comparison == comparison:
                return item
        return None

    def as_dict(self) -> dict[str, Any]:
        """Return the full analysis as a JSON-friendly dictionary."""
        return {
            "strategy": self.strategy,
            "available_sources": self.available_sources,
            "headline": self.headline,
            "findings": self.findings,
            "limitations": self.limitations,
            "pairs": [p.as_dict() for p in self.pairs],
            "advisory": self.advisory,
            "applies_changes": self.applies_changes,
        }


def _round(value: float | None, digits: int = 2) -> float | None:
    """Round a numeric value, preserving missing or invalid values as None.

    Args:
        value: Value to round.
        digits: Number of decimal places to keep.

    Returns:
        Rounded value, or None when value is missing or invalid.
    """
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None
