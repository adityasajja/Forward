"""Application queries for post-trade analytics."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from forward.analytics import aggregation
from forward.analytics.learning_metrics import score_band
from forward.core.analytics_ports import AttributionReaderPort
from forward.core.errors import AnalyticsBookTooLarge, InvalidAnalyticsFilter

VALID_SOURCES = frozenset({"BACKTEST", "PAPER", "LIVE"})
VALID_EVIDENCE_GRADES = frozenset({"forward", "in_sample"})


class AnalyticsService:
    """Load and filter attribution records for analytics reports."""

    def __init__(
        self,
        attribution_reader: AttributionReaderPort,
        *,
        max_aggregate_rows: int,
        min_sample: int,
        slippage_high_bps: float,
    ) -> None:
        """Create the analytics service.

        Args:
            attribution_reader: Port for reading saved attributions.
            max_aggregate_rows: Largest account book accepted by aggregate reports.
            min_sample: Minimum number of observations for a grouped report.
            slippage_high_bps: Configured adverse-slippage warning threshold.
        """
        self._attributions = attribution_reader
        self._max_aggregate_rows = max_aggregate_rows
        self._min_sample = min_sample
        self._slippage_high_bps = slippage_high_bps

    def normalize_filters(
        self,
        *,
        strategy_id: str | None = None,
        strategy_version: int | None = None,
        symbol: str | None = None,
        source: str | None = None,
        provenance: str | None = None,
        market_regime: str | None = None,
        sector: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> dict[str, Any]:
        """Validate and normalize the shared analytics filters.

        Args:
            strategy_id: Optional strategy identifier.
            strategy_version: Optional strategy version.
            symbol: Optional instrument symbol.
            source: Optional BACKTEST, PAPER, or LIVE source.
            provenance: Optional forward or in-sample evidence grade.
            market_regime: Optional recorded regime.
            sector: Optional recorded sector.
            start: Inclusive lower bound for the computed timestamp.
            end: Inclusive upper bound for the computed timestamp.

        Returns:
            Normalized filters for repository and in-memory matching.

        Raises:
            InvalidAnalyticsFilter: If source or provenance is unsupported.
        """
        if source and source.upper() not in VALID_SOURCES:
            allowed = ", ".join(sorted(VALID_SOURCES))
            raise InvalidAnalyticsFilter(f"source must be one of: {allowed}")
        if provenance and provenance.lower() not in VALID_EVIDENCE_GRADES:
            allowed = ", ".join(sorted(VALID_EVIDENCE_GRADES))
            raise InvalidAnalyticsFilter(f"provenance must be one of: {allowed}")
        return {
            "strategy_id": strategy_id,
            "strategy_version": strategy_version,
            "symbol": symbol,
            "source": source.upper() if source else None,
            "evidence_grade": provenance.lower() if provenance else None,
            "market_regime": market_regime,
            "sector": sector,
            "start": start,
            "end": end,
        }

    def aggregate_rows(self, user_id: str, **filters: Any) -> list[dict[str, Any]]:
        """Return every attribution row matching one report's filters.

        Args:
            user_id: Account that owns the attributions.
            **filters: Filters accepted by :meth:`normalize_filters`.

        Returns:
            Every matching row, in the order returned by storage.

        Raises:
            AnalyticsBookTooLarge: If the unfiltered account book exceeds the
                aggregate row limit.
            InvalidAnalyticsFilter: If a filter value is unsupported.
        """
        normalized = self.normalize_filters(**filters)
        rows = self._attributions.all_rows(user_id)
        self._check_aggregate_size(len(rows))
        return [row for row in rows if self._matches(row, normalized)]

    def trade_page(
        self,
        user_id: str,
        *,
        limit: int,
        offset: int,
        reason_code: str | None = None,
        **filters: Any,
    ) -> tuple[list[dict[str, Any]], int, dict[str, Any]]:
        """Return one filtered page and the full matching total.

        Args:
            user_id: Account that owns the attributions.
            limit: Maximum page size.
            offset: Number of rows to skip.
            reason_code: Optional recorded reason code.
            **filters: Filters accepted by :meth:`normalize_filters`.

        Returns:
            Page rows, total matching rows, and normalized filters.

        Raises:
            AnalyticsBookTooLarge: If the account book exceeds the aggregate
                row limit used to calculate the total.
            InvalidAnalyticsFilter: If a filter value is unsupported.
        """
        normalized = self.normalize_filters(**filters)
        repository_filters = {key: value for key, value in normalized.items() if value is not None}
        page = self._attributions.rows(
            user_id,
            limit=limit,
            offset=offset,
            reason_code=reason_code,
            **repository_filters,
        )
        page = [row for row in page if self._matches(row, normalized)]
        all_rows = self._attributions.all_rows(user_id)
        self._check_aggregate_size(len(all_rows))
        total = sum(1 for row in all_rows if self._matches(row, normalized))
        return page, total, normalized

    def trade_attribution(self, user_id: str, trade_id: str) -> dict[str, Any] | None:
        """Return one attribution owned by an account.

        Args:
            user_id: Account that owns the attribution.
            trade_id: Trade identifier.

        Returns:
            The attribution row, or ``None`` when it is not found.
        """
        return self._attributions.get(user_id, trade_id)

    def coverage(self, user_id: str, attributed: int) -> dict[str, Any]:
        """Return attribution coverage for the account's closed trades.

        Args:
            user_id: Account to measure.
            attributed: Number of attributions in the report.

        Returns:
            Coverage counts and completeness.
        """
        return self._attributions.coverage(user_id, attributed)

    def summary_report(self, user_id: str, **filters: Any) -> dict[str, Any]:
        """Build the headline report for the matching attribution rows.

        Args:
            user_id: Account that owns the attributions.
            **filters: Filters accepted by :meth:`normalize_filters`.

        Returns:
            Headline metrics and attribution coverage.
        """
        rows = self.aggregate_rows(user_id, **filters)
        report = aggregation.overview(rows)
        report["attribution_coverage"] = self.coverage(user_id, len(rows))
        return report

    def strategy_report(self, user_id: str, **filters: Any) -> dict[str, Any]:
        """Compare attribution metrics across strategy versions.

        Args:
            user_id: Account that owns the attributions.
            **filters: Filters accepted by :meth:`normalize_filters`.

        Returns:
            Strategy buckets and a note that explains the comparison.
        """
        rows = self.aggregate_rows(user_id, **filters)
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            tree = row.get("attribution") or {}
            signal = tree.get("signal") or {}
            key = str(signal.get("strategy_id") or "unattributed")
            version = signal.get("strategy_version")
            label = f"{key}@v{version}" if version is not None else key
            grouped.setdefault(label, []).append(row)
        return {
            "strategies": [
                {
                    "key": key,
                    "n": len(bucket),
                    "summary": aggregation.overview(bucket),
                    "branches": aggregation.by_branch(bucket),
                }
                for key, bucket in sorted(grouped.items())
            ],
            "note": (
                "A comparison across strategies, not a ranking. A strategy missing "
                "from this list has no attributed trades, which is different from "
                "having performed badly."
            ),
        }

    def regime_report(self, user_id: str, **filters: Any) -> dict[str, Any]:
        """Compare results by the market regime recorded at entry.

        Args:
            user_id: Account that owns the attributions.
            **filters: Filters accepted by :meth:`normalize_filters`.

        Returns:
            Regime buckets and their dimension name.
        """
        rows = self.aggregate_rows(user_id, **filters)
        return {
            "buckets": self._cut(rows, lambda row: row.get("market_regime"), aggregation.overview),
            "dimension": "market_regime",
        }

    def context_report(self, user_id: str, **filters: Any) -> dict[str, Any]:
        """Compare results by recorded context class and score band.

        Args:
            user_id: Account that owns the attributions.
            **filters: Filters accepted by :meth:`normalize_filters`.

        Returns:
            Context-class and score-band buckets with their methodology note.
        """
        rows = self.aggregate_rows(user_id, **filters)
        return {
            "by_class": self._cut(rows, lambda row: row.get("context_class"), aggregation.overview),
            "by_score_band": self._cut(
                rows,
                lambda row: score_band(row.get("context_score")),
                aggregation.overview,
                min_sample=self._min_sample,
            ),
            "by_score_band_note": (
                "Score bands are the recorded score bucketed, not a re-score under "
                "a different model version."
            ),
        }

    def sizing_report(self, user_id: str, **filters: Any) -> dict[str, Any]:
        """Compare results by sizing method and cap status.

        Args:
            user_id: Account that owns the attributions.
            **filters: Filters accepted by :meth:`normalize_filters`.

        Returns:
            Sizing-method and cap-status buckets.
        """
        rows = self.aggregate_rows(user_id, **filters)
        return {
            "by_method": self._cut(
                rows, lambda row: row.get("sizing_method"), aggregation.overview
            ),
            "by_cap": self._cut(rows, self._cap_bucket, aggregation.overview),
        }

    def execution_report(self, user_id: str, **filters: Any) -> dict[str, Any]:
        """Compare execution quality and slippage for matching trades.

        Args:
            user_id: Account that owns the attributions.
            **filters: Filters accepted by :meth:`normalize_filters`.

        Returns:
            Execution-quality buckets and the full execution distribution.
        """
        rows = self.aggregate_rows(user_id, **filters)
        return {
            "by_quality": self._cut(
                rows, lambda row: row.get("execution_quality"), aggregation.overview
            ),
            "by_entry_quality": self._cut(
                rows, lambda row: row.get("entry_quality"), aggregation.overview
            ),
            "by_slippage_bucket": self._cut(
                rows,
                lambda row: aggregation.bucket_slippage(row.get("total_slippage_bps")),
                aggregation.overview,
                min_sample=self._min_sample,
            ),
            "distribution": {
                **aggregation.execution(rows, min_sample=self._min_sample),
                "slippage_high_bps": self._slippage_high_bps,
            },
        }

    def mae_mfe_report(
        self, user_id: str, *, include_points: bool, **filters: Any
    ) -> dict[str, Any]:
        """Build the trade excursion report and its measurement notes.

        Args:
            user_id: Account that owns the attributions.
            include_points: Whether to include each trade's scatter point.
            **filters: Filters accepted by :meth:`normalize_filters`.

        Returns:
            Excursion distributions, optional points, and methodology.
        """
        rows = self.aggregate_rows(user_id, **filters)
        report = aggregation.mae_mfe(rows)
        if not include_points:
            report.pop("points", None)
        report["methodology"] = {
            "window": "bars timestamped between entry and exit, both bounds inclusive",
            "source": "each bar's high and low, never its close",
            "sign": (
                "MFE favourable-positive, MAE adverse-negative, both in the position's direction"
            ),
            "rupees": "the percentage figure applied to the position's entry value",
            "risk_multiple": "excursion in rupees divided by the planned risk amount",
            "look_ahead": (
                "the window stops at the exit, so a trade's excursion never includes "
                "price action the position did not live through"
            ),
            "missing": "no usable bars between entry and exit leaves every figure null",
        }
        return report

    @staticmethod
    def _cut(
        rows: list[dict[str, Any]],
        keyer: Callable[[dict[str, Any]], Any],
        summariser: Callable[[list[dict[str, Any]]], dict[str, Any]],
        *,
        min_sample: int = 1,
    ) -> list[dict[str, Any]]:
        """Group rows and suppress statistics below the requested sample size."""
        buckets: dict[str, list[dict[str, Any]]] = {}
        excluded = 0
        for row in rows:
            key = keyer(row)
            if key is None:
                excluded += 1
                continue
            buckets.setdefault(str(key), []).append(row)
        output = []
        for key in sorted(buckets):
            bucket = buckets[key]
            summary = summariser(bucket)
            summary["key"] = key
            summary["suppressed"] = len(bucket) < min_sample
            output.append(summary)
        if excluded:
            output.append({"key": "not_measured", "n": excluded, "suppressed": True})
        return output

    @staticmethod
    def _cap_bucket(row: dict[str, Any]) -> str | None:
        """Classify whether a recorded sizing cap bound the position size."""
        if row.get("sizing_cap_reason"):
            return "cap_bound"
        if row.get("sizing_method"):
            return "no_cap_recorded"
        return None

    def _check_aggregate_size(self, row_count: int) -> None:
        """Reject aggregate work that would load an oversized book."""
        maximum = self._max_aggregate_rows
        if row_count > maximum:
            raise AnalyticsBookTooLarge(
                f"book too large ({row_count} rows) to aggregate in one call — "
                "narrow the filters (strategy, symbol, or date range) and retry"
            )

    @classmethod
    def _matches(cls, row: dict[str, Any], filters: dict[str, Any]) -> bool:
        """Return whether an attribution row matches all supplied filters."""
        tree = row.get("attribution") or {}
        signal = tree.get("signal") or {}
        if filters.get("strategy_id") and str(signal.get("strategy_id") or "") != str(
            filters["strategy_id"]
        ):
            return False
        if (
            filters.get("strategy_version") is not None
            and signal.get("strategy_version") != filters["strategy_version"]
        ):
            return False
        if filters.get("symbol") and cls._upper(row.get("symbol")) != cls._upper(filters["symbol"]):
            return False
        if filters.get("source") and str(row.get("source") or "").upper() != filters["source"]:
            return False
        if filters.get("evidence_grade"):
            grade = str(row.get("evidence_grade") or "in_sample").lower()
            if grade != filters["evidence_grade"]:
                return False
        if filters.get("market_regime") and row.get("market_regime") != filters["market_regime"]:
            return False
        if filters.get("sector") and row.get("sector") != filters["sector"]:
            return False
        moment = row.get("computed_at")
        if filters.get("start") is not None and (
            moment is None or moment < cls._naive(filters["start"])
        ):
            return False
        return filters.get("end") is None or (
            moment is not None and moment <= cls._naive(filters["end"])
        )

    @staticmethod
    def _naive(value: datetime) -> datetime:
        """Convert a timezone-aware filter to naive UTC-style storage time."""
        if value.tzinfo is None:
            return value
        return value.astimezone(UTC).replace(tzinfo=None)

    @staticmethod
    def _upper(value: Any) -> str:
        """Normalize a symbol for case-insensitive comparison."""
        return str(value or "").strip().upper()
