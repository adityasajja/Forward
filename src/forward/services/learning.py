"""Coordinate learning reports from stored trading evidence.

The service joins dataset assembly, analysis, daily reports, storage ports, and
report files. It records advisory observations and never changes strategies or
deployments.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from forward.config.settings import get_settings
from forward.core.historical_market_data_ports import CachedMarketFramesPort
from forward.core.learning_evidence import (
    CLASS_LIVE_FORWARD,
    CLASS_PAPER_FORWARD,
    GRADE_FORWARD,
    is_forward_grade,
    row_grade,
)
from forward.core.learning_ports import (
    DailyLearningStoragePort,
    LearningArtifactsPort,
    LearningStoragePort,
)
from forward.core.portfolio_ports import (
    PortfolioStoragePort,
)
from forward.core.session import IST
from forward.research.learning_axes import (
    AXES_BY_NAME,
    DEFAULT_AXES,
    requested_but_unavailable,
)
from forward.research.learning_counts import summarize_forward_evidence
from forward.research.learning_drift import analyse as drift_analysis
from forward.research.learning_drift_models import DriftAnalysis
from forward.services.learning_analysis import (
    PerformanceAnalysis,
    StrategyAnalysis,
    bucket_source_trade_refs,
    matches_strategy,
    resolve_metric,
)
from forward.services.learning_context import (
    query_market_context_performance as build_market_context_performance,
)
from forward.services.learning_dataset import LearningDatasetBuilder
from forward.services.learning_dataset_model import LearningDataset, _iso
from forward.services.learning_report_model import DailyReport
from forward.services.learning_reports import DailyLearningReport

# ---------------------------------------------------------------------------
# service
# ---------------------------------------------------------------------------


class LearningService:
    """The entry point every caller uses. Reads the book; writes two artefacts.

    Deliberately has no method that changes a strategy. If one is ever added it
    will be for *proposing* a change, and it will live in a different module with
    an approval gate in front of it — not here, where a report built on
    unvalidated findings could call it.
    """

    def __init__(
        self,
        *,
        storage: LearningStoragePort,
        portfolio_storage: PortfolioStoragePort,
        daily_learning_storage: DailyLearningStoragePort,
        market_history: CachedMarketFramesPort,
        artifacts: LearningArtifactsPort,
        builder: LearningDatasetBuilder | None = None,
    ) -> None:
        """Create the learning use case with its required portfolio storage.

        Args:
            storage: Port used to read learning evidence and observations.
            portfolio_storage: Port used by dataset builds for portfolio context.
            daily_learning_storage: Port used to write observations and read cycles.
            market_history: Port used by dataset builds to read market frames.
            artifacts: Port used for learning input and report files.
            builder: Dataset builder supplied by the application or a test.
        """
        self._storage = storage
        self._portfolio_storage = portfolio_storage
        self._daily_learning_storage = daily_learning_storage
        self._market_history = market_history
        self._artifacts = artifacts
        self._builder = builder
        self._datasets: dict[str | None, LearningDataset] = {}
        self._dataset_built_at: dict[str | None, float] = {}
        # Single-flight: the build is CPU-bound pandas work that holds the GIL, so
        # N concurrent callers building the same dataset in parallel is N times
        # the stall for every other request, and the same answer N times.
        self._dataset_lock = threading.Lock()

    def dataset(self, *, refresh: bool = False, user_id: str | None = None) -> LearningDataset:
        """Return the learning dataset, cached separately for each account.

        Args:
            refresh: Rebuild after the shorter refresh interval.
            user_id: Account whose private trading evidence is included.

        Returns:
            The account-scoped learning dataset.
        """

        def stale() -> bool:
            dataset = self._datasets.get(user_id)
            built_at = self._dataset_built_at.get(user_id, 0.0)
            age = time.monotonic() - built_at
            settings = get_settings()
            ttl = (
                settings.learning_dataset_refresh_ttl_seconds
                if refresh
                else settings.learning_dataset_ttl_seconds
            )
            return dataset is None or age > ttl

        if stale():
            with self._dataset_lock:
                # Re-check: a caller that waited on the lock finds the dataset the
                # first one just built, instead of building it again.
                if stale():
                    builder = self._builder or LearningDatasetBuilder(
                        storage=self._storage,
                        portfolio_storage=self._portfolio_storage,
                        market_history=self._market_history,
                        artifacts=self._artifacts,
                    )
                    self._datasets[user_id] = builder.build(user_id=user_id)
                    self._dataset_built_at[user_id] = time.monotonic()
        return self._datasets[user_id]

    def axes_catalog(self) -> dict[str, Any]:
        """Describe the available, default, and unavailable analysis axes."""
        return {
            "available": {
                name: {"label": axis.label, "max_values": axis.max_values}
                for name, axis in sorted(AXES_BY_NAME.items())
            },
            "default": [axis.name for axis in DEFAULT_AXES],
            "unavailable": requested_but_unavailable(),
        }

    def axis_names(self) -> tuple[str, ...]:
        """Return names accepted by the performance analysis."""
        return tuple(AXES_BY_NAME)

    def performance(
        self,
        *,
        strategy: str | None = None,
        roles: Iterable[str] | None = None,
        classes: Iterable[str] | None = None,
        grades: Iterable[str] | None = None,
        axes: list[str] | None = None,
        metric: str | None = None,
        min_sample: int | None = None,
        refresh: bool = False,
        user_id: str | None = None,
    ) -> StrategyAnalysis:
        kwargs: dict[str, Any] = {}
        if min_sample is not None:
            kwargs["min_sample"] = min_sample
        analysis = PerformanceAnalysis(
            self.dataset(refresh=refresh, user_id=user_id), metric=metric
        )
        return analysis.analyse(
            strategy, roles=roles, classes=classes, grades=grades, axes=axes, **kwargs
        )

    def daily_report(
        self,
        *,
        as_of: datetime | None = None,
        window_days: int = get_settings().learning_forward_window_days,
        metric: str | None = None,
        grades: Iterable[str] | None = None,
        classes: Iterable[str] | None = None,
        refresh: bool = False,
        user_id: str | None = None,
    ) -> DailyReport:
        """The end-of-day report.

        ``grades`` narrows the evidence the report may draw on; the default is
        both, because the report's first job is to say what happened. What gates
        a *claim* is the forward-observation count, not this filter — see
        :class:`DailyLearningReport`.

        ``user_id`` scopes the report to one account. Omitting it is reserved
        for whole-book scheduler reports.
        """
        return DailyLearningReport(
            self.dataset(refresh=refresh, user_id=user_id),
            metric=metric,
            grades=grades,
            classes=classes,
        ).build(as_of=as_of, window_days=window_days)

    def drift(
        self,
        *,
        strategy: str | None = None,
        reference: str | None = None,
        roles: Iterable[str] | None = None,
        min_sample: int | None = None,
        refresh: bool = False,
        user_id: str | None = None,
    ) -> DriftAnalysis:
        """Backtest vs paper vs live, per strategy.

        ``roles`` restricts which sources take part, which is what a caller
        wants when asking "how is live doing against the backtest?" and not
        wanting paper in the middle of it.
        """
        rows = self.dataset(refresh=refresh, user_id=user_id).rows
        if roles:
            allowed = {str(role).upper() for role in roles}
            rows = [row for row in rows if str(row.get("source") or "").upper() in allowed]
        kwargs: dict[str, Any] = {}
        if min_sample is not None:
            kwargs["min_sample"] = min_sample
        return drift_analysis(rows, strategy=strategy, reference=reference, **kwargs)

    def snapshot(self, *, user_id: str | None = None, write: bool = True) -> dict[str, Any]:
        """Build everything and optionally persist it through the artifact port.

        Writing is opt-in so a read-only caller (a route rendering a page) does
        not mutate the filesystem, and so a test can exercise the whole pipeline
        without leaving artefacts behind.
        """
        dataset = self.dataset(refresh=True, user_id=user_id)
        report = DailyLearningReport(dataset).build()
        analysis = PerformanceAnalysis(dataset).analyse()
        drift = drift_analysis(dataset.rows)

        payload = {
            "generated_at": _iso(dataset.generated_at),
            "dataset": dataset.summary(),
            "report": report.as_dict(),
            "analysis": analysis.as_dict(),
            "drift": drift.as_dict(),
        }
        if write:
            payload["written"] = dict(self._artifacts.write_snapshot(payload))
        return payload

    def status(self, user_id: str | None = None) -> dict[str, Any]:
        """What the engine has to work with, without building the dataset.

        Cheap by design, so a UI can render "nothing to learn from yet" before
        paying for a full build. Counts are filtered to the account that called
        the route.

        **The counts are split by evidence grade, not just by source.** A total
        that said "312 trades available" while none of them was forward would be
        the same lie in a different place: the number an operator reads first
        would say there is plenty to learn from, and the number that decides
        whether anything may be claimed is not on the screen at all.
        """
        try:
            counts = self._storage.evidence_counts(GRADE_FORWARD, user_id)
        except Exception as exc:  # noqa: BLE001
            return {
                "available": False,
                "reason": f"the database could not be read: {type(exc).__name__}",
                "counts": {},
            }

        counts["trade_journal_in_sample"] = (
            counts["trade_journal"] - counts["trade_journal_forward"]
        )

        #: A backtest is in-sample by construction, and a journal row without an
        #: execution-time provenance stamp cannot demonstrate it predates its outcome.
        forward_available = counts["trade_journal_forward"]

        total = counts["backtest_trades"] + counts["trade_journal"]
        if total == 0:
            note = "nothing to analyse yet; the dataset and report will build correctly and say so"
        elif forward_available == 0:
            note = (
                f"{total} trades available and not one is forward evidence: every "
                "row is a backtest (in-sample by construction) or a journal "
                "trade with no execution-time provenance stamp — so the book "
                "supports no claim about whether anything still works"
            )
        else:
            note = (
                f"{total} trades available, {forward_available} of them forward "
                f"evidence ({total - forward_available} in-sample)"
            )
        return {
            "available": True,
            "counts": counts,
            "trades_available": total,
            "forward_available": forward_available,
            "sufficient_for_analysis": total >= get_settings().learning_min_sample,
            #: Whether anything may be *claimed*, which is a different question
            #: from whether there is enough to analyse. Two flags because one
            #: number cannot answer both.
            "sufficient_for_a_claim": (
                forward_available >= get_settings().learning_min_sample
            ),
            "missing_features": requested_but_unavailable(),
            "note": note,
        }

    def forward_evidence_counts(
        self,
        *,
        strategy_id: str | None = None,
        strategy_version: int | None = None,
        refresh: bool = False,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Prominent metrics for Genuine Forward Observations.

        Aggregates counts across:
        - strategy
        - strategy version
        - today
        - last 7 trading days
        - total

        Clearly distinguishes:
        - IN_SAMPLE
        - PAPER_FORWARD
        - LIVE_FORWARD
        """
        now_ist = datetime.now(IST)
        dataset = self.dataset(refresh=refresh, user_id=user_id)
        return summarize_forward_evidence(
            dataset.rows,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            as_of=now_ist,
        )

    def readiness(
        self,
        *,
        strategy_id: str | None = None,
        refresh: bool = False,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Forward-learning readiness, per strategy: is trustworthy evidence accumulating?

        Read-only. Every figure derives from the dataset plus registry reads
        (strategy names/versions, deployments, recorded observations and
        contexts, latest cycle) — nothing here writes, proposes, pauses or
        deploys. States are research labels from
        :mod:`forward.research.learning_readiness`; see that module for the gates
        and the caps that keep a bare trade count from reading as ready.
        """
        from forward.research import learning_readiness as lr

        dataset = self.dataset(refresh=refresh, user_id=user_id)
        all_forward = [row for row in dataset.rows if is_forward_grade(row_grade(row))]
        unique, dropped = lr.dedupe_forward(all_forward)

        registry = self._readiness_registry(user_id=user_id)
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in unique:
            key = str(row.get("strategy_id") or row.get("strategy_key") or "UNKNOWN")
            if strategy_id and key != strategy_id:
                continue
            groups.setdefault(key, []).append(row)

        strategies = [self._strategy_readiness(key, rows, registry) for key, rows in groups.items()]
        strategies.sort(key=lambda s: (-s["forward_trades"], s["strategy_name"]))

        quality_issues = lr.data_quality_issues(lr.forward_closed(unique), duplicate_refs=dropped)

        limitations: list[str] = []
        if user_id is None:
            limitations.append(
                "no user scope was given, so strategy names, versions and "
                "deployments could not be resolved from the registry"
            )
        if dropped:
            limitations.append(
                f"{len(dropped)} duplicate trade reference(s) were counted once; "
                "see the duplicate_trade_ref quality issue"
            )

        return {
            "generated_at": _iso(dataset.generated_at),
            "gates": list(lr.EVIDENCE_GATES),
            "forward_trades_unique": len(unique),
            "duplicates_dropped": len(dropped),
            "strategies": strategies,
            "quality_issues": quality_issues,
            "limited": bool(limitations),
            "limitations": limitations,
            # Stated in the payload so a client cannot infer that anything is
            # applied, whatever it renders.
            "advisory_only": True,
            "applies_changes": False,
        }

    def _readiness_registry(self, *, user_id: str | None) -> dict[str, Any]:
        """Registry facts the readiness view needs. Best effort throughout: a
        store that cannot be read degrades to unattributed display, never to a
        failed readiness call."""
        registry: dict[str, Any] = {
            "names": {},
            "current_versions": {},
            "deployments": {},
            "recorded_observations": {},
            "recorded_contexts": {},
            "latest_cycle": None,
        }
        if user_id is None:
            return registry
        try:
            for row in self._portfolio_storage.strategies(user_id):
                sid = str(row.get("strategy_id"))
                registry["names"][sid] = row.get("name") or sid
                if row.get("latest_version") is not None:
                    registry["current_versions"][sid] = row.get("latest_version")
            for row in self._portfolio_storage.deployments(user_id):
                sid = str(row.get("strategy_id") or "")
                previous = registry["deployments"].get(sid)
                if previous is None or str(row.get("created_at") or "") >= str(
                    previous.get("created_at") or ""
                ):
                    registry["deployments"][sid] = {
                        "mode": row.get("mode"),
                        "status": row.get("status"),
                    }
            registry["recorded_observations"] = self._storage.observation_counts_by_strategy(
                (CLASS_PAPER_FORWARD, CLASS_LIVE_FORWARD), user_id
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("learning: readiness registry read failed: {}", exc)
            return registry
        try:
            for strategy_id in list(registry["names"]):
                try:
                    registry["recorded_contexts"][strategy_id] = self._storage.signal_context_count(
                        user_id, strategy_id
                    )
                except Exception:  # noqa: BLE001 — one strategy must not fail the view
                    continue
        except Exception as exc:  # noqa: BLE001
            logger.warning("learning: readiness context counts failed: {}", exc)
        try:
            registry["latest_cycle"] = self._daily_learning_storage.latest_cycle(user_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("learning: readiness latest cycle unreadable: {}", exc)
        return registry

    def _strategy_readiness(
        self, key: str, rows: list[dict[str, Any]], registry: dict[str, Any]
    ) -> dict[str, Any]:
        from forward.research import learning_readiness as lr

        closed = [r for r in rows if r.get("exit_ts") is not None]
        open_count = len(rows) - len(closed)
        group_dataset = LearningDataset(
            rows=closed, missing_features={}, generated_at=datetime.now(UTC)
        )
        metric, metric_note = resolve_metric(group_dataset, None)
        summary = lr.summarise_forward(closed, metric=metric)
        progression = lr.weekly_progression(closed)
        issues = lr.data_quality_issues(closed, metric=metric)

        n_with_metric = summary["n_with_metric"]
        n_with_context = summary["score_coverage"]["with_score"]
        inconsistent_count = next(
            (i["count"] for i in issues if i["code"] == "inconsistent_provenance"),
            0,
        )
        gaps = lr.blocking_gaps(
            n_with_metric=n_with_metric,
            n_with_context=n_with_context,
            n_inconsistent_provenance=inconsistent_count,
        )
        recorded = int(registry["recorded_observations"].get(key, 0))
        state, reasons = lr.research_state(
            summary["n"],
            recorded_forward_observations=recorded,
            blocking_gaps=gaps,
        )

        names = registry["names"]
        latest_cycle = registry.get("latest_cycle") or {}
        analysed = latest_cycle.get("strategies_analyzed") or []

        missing: list[str] = []
        missing_feature_limit = get_settings().learning_missing_feature_display_limit
        for row in closed:
            for name in row.get("missing_features") or []:
                if name not in missing:
                    missing.append(name)
                if len(missing) >= missing_feature_limit:
                    break
            if len(missing) >= missing_feature_limit:
                break

        exits = [r.get("exit_ts") for r in closed if r.get("exit_ts") is not None]
        versions = sorted(
            {r.get("strategy_version") for r in rows if r.get("strategy_version") is not None},
            key=str,
        )

        return {
            "strategy_id": key,
            "strategy_name": names.get(key, key if key != "UNKNOWN" else "Unattributed"),
            "versions_seen": versions,
            "current_version": registry["current_versions"].get(key),
            "deployment": registry["deployments"].get(key),
            "forward_trades": summary["n"],
            "open_forward_trades": open_count,
            "state": state,
            "state_reasons": reasons,
            "next_gate": {
                "required": lr.next_gate(summary["n"]),
                "have": summary["n"],
                "status": lr.evidence_status(summary["n"]),
            },
            "summary": summary,
            "metric_note": metric_note,
            "progression": progression,
            "context_observations_recorded": int(registry["recorded_contexts"].get(key, 0)),
            "forward_observations_recorded": recorded,
            "optimization_ready": state == lr.STATE_OPTIMIZATION_ELIGIBLE,
            "missing_features": missing,
            "last_trade": _iso(max(exits)) if exits else None,
            "last_cycle": (
                {
                    "cycle_id": latest_cycle.get("cycle_id"),
                    "execution_date": latest_cycle.get("execution_date"),
                    "status": latest_cycle.get("status"),
                    "strategy_included": key in analysed,
                }
                if latest_cycle
                else None
            ),
            "quality_issues": issues,
        }

    def backtest_vs_forward(
        self,
        *,
        strategy: str | None = None,
        strategy_version: int | None = None,
        min_sample: int | None = None,
        refresh: bool = False,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Specific side-by-side comparison of BACKTEST vs PAPER_FORWARD evidence.

        Compares trade count, win rate, average return, profit factor, drawdown,
        and return distributions without judging strategy as good or bad.
        """
        rows = self.dataset(refresh=refresh, user_id=user_id).rows
        if strategy:
            rows = [r for r in rows if matches_strategy(r, strategy)]
        if strategy_version is not None:
            rows = [r for r in rows if r.get("strategy_version") == strategy_version]

        backtest_rows = [
            r
            for r in rows
            if str(r.get("evidence_class") or "").upper() in ("BACKTEST", "IN_SAMPLE")
            or str(r.get("source") or "").upper() == "BACKTEST"
        ]
        forward_rows = [
            r
            for r in rows
            if str(r.get("evidence_class") or "").upper() == "PAPER_FORWARD"
            or (
                str(r.get("source") or "").upper() == "PAPER"
                and str(r.get("evidence_grade") or "").lower() == "forward"
            )
        ]

        from forward.research.learning_drift import compare_sources

        drift_pair = compare_sources(
            backtest_rows,
            forward_rows,
            reference="BACKTEST",
            comparison="PAPER_FORWARD",
            min_sample=(
                min_sample
                if min_sample is not None
                else get_settings().learning_min_sample
            ),
        )

        return {
            "strategy": strategy or "ALL",
            "strategy_version": strategy_version,
            "backtest_n": len(backtest_rows),
            "forward_n": len(forward_rows),
            "comparison": drift_pair.as_dict(),
            "findings": drift_pair.findings,
            "limitations": drift_pair.limitations,
            "distribution": drift_pair.distribution,
            "regime": drift_pair.regime,
        }

    def record_observations(
        self,
        *,
        strategy_id: str | None = None,
        strategy_version: int | None = None,
        min_sample: int,
        refresh: bool = False,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Persist structured learning observations/findings to database.

        Analyzes PAPER_FORWARD trades by condition buckets and records findings
        with sample sizes, statistical results, confidence, and trade IDs.
        """
        dataset = self.dataset(refresh=refresh, user_id=user_id)
        forward_rows = [
            r
            for r in dataset.rows
            if str(r.get("evidence_class") or "").upper() == "PAPER_FORWARD"
            or (
                str(r.get("source") or "").upper() == "PAPER"
                and str(r.get("evidence_grade") or "").lower() == "forward"
            )
        ]
        if strategy_id:
            forward_rows = [r for r in forward_rows if matches_strategy(r, strategy_id)]

        if not forward_rows:
            return []

        analysis = PerformanceAnalysis(
            LearningDataset(
                rows=forward_rows,
                missing_features={},
                generated_at=dataset.generated_at,
            )
        ).analyse(
            strategy=strategy_id,
            min_sample=min_sample,
            # Defaults plus cost drag: the findings loop must see friction as a
            # first-class slice (costs drowning gross is today's lesson), while
            # the shared defaults stay lean for everyone else's p-values.
            axes=[axis.name for axis in DEFAULT_AXES] + ["cost_drag"],
        )

        today_str = datetime.now(UTC).strftime("%Y-%m-%d")
        observations = []
        for breakdown in analysis.breakdowns:
            for bucket in breakdown.buckets:
                if bucket.get("suppressed"):
                    continue
                # Gate on independent observations, not raw rows: fourteen
                # fills in one minute is one piece of evidence wearing a
                # trenchcoat, and recording it at n=14 would let a single
                # market moment mint a finding.
                n = bucket.get("n", 0)
                n_eff = bucket.get("n_effective", n)
                try:
                    n_eff = float(n_eff)
                except TypeError, ValueError:
                    n_eff = float(n or 0)
                if n_eff < min_sample:
                    continue
                condition_bucket = f"{breakdown.axis}:{bucket.get('label')}"
                p_val = bucket.get("p_adjusted")
                conf = (
                    (1.0 - p_val)
                    if p_val is not None
                    else (
                        get_settings().learning_confidence_strong
                        if bucket.get("significance") in ("strong", "moderate")
                        else get_settings().learning_confidence_other
                    )
                )
                source_ids = bucket_source_trade_refs(
                    forward_rows,
                    breakdown.axis,
                    str(bucket["label"]),
                    analysis.metric,
                )
                observations.append(
                    {
                        "strategy_id": strategy_id or analysis.strategy,
                        "strategy_version": strategy_version,
                        "date": today_str,
                        "metric": analysis.metric,
                        "condition_bucket": condition_bucket,
                        "sample_size": n,
                        "statistical_result": bucket,
                        "evidence_class": "PAPER_FORWARD",
                        "confidence": conf,
                        "source_trades": source_ids,
                    }
                )

        return self._daily_learning_storage.record_observations(observations)

    def query_market_context_performance(
        self,
        *,
        strategy: str | None = None,
        condition_axis: str,
        condition_value: str | None = None,
        grades: Iterable[str] | None = None,
        metric: str | None = None,
        min_sample: int | None = None,
        refresh: bool = False,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Answer how a strategy performs under one market condition.

        Args:
            strategy: Optional strategy name or identifier.
            condition_axis: Dataset field or supported market-context alias.
            condition_value: Optional bucket label to select.
            grades: Optional evidence grades to include.
            metric: Optional outcome field requested by the caller.
            min_sample: Minimum bucket size before results are unsuppressed.
            refresh: Whether to rebuild the cached learning dataset.
            user_id: Optional user whose evidence should be analyzed.

        Returns:
            Baseline and per-bucket statistics with forward evidence counts.
        """
        dataset = self.dataset(refresh=refresh, user_id=user_id)
        resolved_metric, metric_note = resolve_metric(dataset, metric)
        minimum = (
            min_sample
            if min_sample is not None
            else get_settings().learning_min_sample
        )
        return build_market_context_performance(
            dataset.rows,
            strategy=strategy,
            condition_axis=condition_axis,
            condition_value=condition_value,
            grades=grades,
            resolved_metric=resolved_metric,
            metric_note=metric_note,
            min_sample=minimum,
            bucket_thresholds=get_settings().learning_market_context_thresholds,
        )

    def list_observations(
        self,
        *,
        user_id: str | None = None,
        strategy_id: str | None = None,
        strategy_version: int | None = None,
        evidence_class: str | None = None,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Retrieve stored learning observation history from database."""
        return self._storage.list_observations(
            user_id=user_id,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            evidence_class=evidence_class,
            limit=limit,
        )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# why a row carries the grade it does
# ---------------------------------------------------------------------------
#
# Every row states the basis of its own grade. A grade with no stated basis is a
# label the reader has to take on trust, and the whole point of the column is
# that it should not have to be.

_service: LearningService | None = None
_service_lock = threading.Lock()


def get_learning_service(
    storage: LearningStoragePort,
    portfolio_storage: PortfolioStoragePort,
    daily_learning_storage: DailyLearningStoragePort,
    market_history: CachedMarketFramesPort,
    artifacts: LearningArtifactsPort,
) -> LearningService:
    """Return the process learning service with its storage adapters.

    Args:
        storage: Adapter used to read learning evidence.
        portfolio_storage: Adapter used for portfolio context during dataset builds.
        daily_learning_storage: Adapter used for observations and cycle reports.
        market_history: Adapter used for daily bars and benchmark data.
        artifacts: Adapter used for learning inputs and report files.

    Returns:
        The process-wide learning service.
    """
    global _service
    with _service_lock:
        if _service is None:
            _service = LearningService(
                storage=storage,
                portfolio_storage=portfolio_storage,
                daily_learning_storage=daily_learning_storage,
                market_history=market_history,
                artifacts=artifacts,
            )
        elif (
            _service._storage is not storage
            or _service._portfolio_storage is not portfolio_storage
            or _service._daily_learning_storage is not daily_learning_storage
            or _service._market_history is not market_history
            or _service._artifacts is not artifacts
        ):
            raise RuntimeError("learning service was configured with different adapters")
        return _service


def reset_learning_service() -> None:
    global _service
    with _service_lock:
        _service = None
