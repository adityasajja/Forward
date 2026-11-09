"""Automated Daily Learning Cycle Service.

Pipeline:
PAPER_FORWARD TRADES
→ DATASET UPDATE
→ STATISTICAL ANALYSIS
→ PATTERN DETECTION
→ HYPOTHESIS GENERATION
→ OPTIMIZATION CANDIDATE
→ RESEARCH QUEUE / PERSISTENCE

Guarantees & Constraints:
- Runs after market session (or on-demand).
- Collects newly closed PAPER_FORWARD trades.
- Reuses existing PerformanceAnalysis, DailyLearningReport, and compare_sources.
- Evaluates forward trades across all declared axes.
- Enforces strict safeguards: suppresses n < 10, statistically weak findings, and isolated outliers.
- Feeds eligible hypotheses into the existing controlled optimization layer.
- Never automatically approves recommendations or changes live strategies.
- Completely reproducible: records cycle ID, execution date, strategies analyzed,
  trades processed, observations generated, hypotheses generated, candidates,
  recommendations, errors, and runtime.
- Never manufactures forward evidence from backtests or backfills.
- Returns comprehensive failure reasons when data or sample size is insufficient.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from loguru import logger

from forward.config.settings import get_settings
from forward.core.learning_evidence import CLASS_PAPER_FORWARD
from forward.core.learning_ports import (
    DailyLearningStoragePort,
)
from forward.core.session import IST
from forward.core.time import utcnow
from forward.optimization.adaptive import extract_adaptive_parameters
from forward.optimization.candidates import (
    OptimizationCandidate,
    generate_candidates_from_observations,
)
from forward.research.learning_drift import compare_sources
from forward.services.learning import LearningService
from forward.services.learning_analysis import (
    PerformanceAnalysis,
    bucket_source_trade_refs,
)
from forward.services.learning_dataset_model import LearningDataset
from forward.services.optimization import OptimizationService

if TYPE_CHECKING:
    from forward.signal_context.service import SignalContextService


@dataclass
class ResearchHypothesis:
    """A research candidate statement derived from empirical forward evidence."""

    hypothesis_id: str
    strategy_id: str
    strategy_version: int | None
    parameter_target: str
    statement: str
    condition_bucket: str
    sample_size: int
    baseline_mean: float | None
    bucket_mean: float | None
    lift: float | None
    p_value: float | None
    confidence: str
    evidence_class: str = CLASS_PAPER_FORWARD
    created_at: str = field(default_factory=lambda: utcnow().isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DailyLearningCycleReport:
    """Result of an automated daily learning cycle execution."""

    cycle_id: str
    execution_date: str
    started_at: str
    completed_at: str
    runtime_seconds: float
    status: str  # SUCCESS, NO_DATA, INSUFFICIENT_SAMPLE, PARTIAL, ERROR
    strategies_analyzed: list[str]
    trades_processed: int
    new_forward_trades_count: int
    observations_generated: int
    hypotheses_generated: int
    candidates_generated: int
    recommendations_generated: int
    recommendation_ids: list[str]
    drift_detected: dict[str, bool]
    insufficient_data_strategies: list[str]
    notes: list[str]
    errors: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DailyLearningCycleService:
    """Orchestrates end-of-day learning cycle across forward trading observations."""

    def __init__(
        self,
        storage: DailyLearningStoragePort,
        learning_service: LearningService,
        optimization_service: OptimizationService,
        context_service: SignalContextService,
    ) -> None:
        """Create the learning cycle with all required use cases and adapters.

        Args:
            storage: Port for observations and cycle reports.
            learning_service: Use case that builds account-scoped trade evidence.
            optimization_service: Use case that reads versions and evaluates candidates.
            context_service: Use case that records signal-context observations.
        """
        self._storage = storage
        self._learning_service = learning_service
        self._optimization_service = optimization_service
        self._context_service = context_service

    def run_cycle(
        self,
        *,
        as_of: datetime | None = None,
        strategy_id: str | None = None,
        min_sample_size: int | None = None,
        user_id: str,
        feed: Any = None,
    ) -> DailyLearningCycleReport:
        """Execute one complete automated daily learning cycle.

        1. Refresh and collect genuine PAPER_FORWARD trades.
        2. Run PerformanceAnalysis across all axes with the configured minimum sample size.
        3. Run DriftAnalysis (BACKTEST vs PAPER_FORWARD).
        4. Generate ResearchHypotheses for statistically significant observations.
        5. Feed hypotheses into OptimizationService to evaluate adaptive candidates.
        6. Persist observations, recommendations, and cycle execution metrics.

        Args:
            as_of: Optional time used to label the cycle's execution date.
            strategy_id: Optional strategy identifier to limit the cycle.
            min_sample_size: Minimum evidence count for analysis and candidates.
            user_id: Account whose forward evidence the cycle may read.
            feed: Historical data feed supplied to candidate evaluation.

        Returns:
            The recorded outcome and diagnostics for this cycle.
        """
        start_wall = time.perf_counter()
        if min_sample_size is None:
            min_sample_size = get_settings().learning_daily_min_observations
        cycle_id = f"cycle_{uuid.uuid4().hex[:12]}"
        now_ist = as_of or datetime.now(IST)
        execution_date = now_ist.strftime("%Y-%m-%d")
        started_at = utcnow().isoformat()

        notes: list[str] = []
        errors: list[str] = []
        recommendation_ids: list[str] = []
        strategies_analyzed: list[str] = []
        insufficient_data_strategies: list[str] = []
        drift_detected: dict[str, bool] = {}

        # 1. Update Learning Dataset with latest forward paper trades
        try:
            dataset: LearningDataset = self._learning_service.dataset(refresh=True, user_id=user_id)
        except Exception as exc:
            logger.exception("Learning cycle failed to build dataset: {}", exc)
            return DailyLearningCycleReport(
                cycle_id=cycle_id,
                execution_date=execution_date,
                started_at=started_at,
                completed_at=utcnow().isoformat(),
                runtime_seconds=round(time.perf_counter() - start_wall, 3),
                status="ERROR",
                strategies_analyzed=[],
                trades_processed=0,
                new_forward_trades_count=0,
                observations_generated=0,
                hypotheses_generated=0,
                candidates_generated=0,
                recommendations_generated=0,
                recommendation_ids=[],
                drift_detected={},
                insufficient_data_strategies=[],
                notes=["Dataset build failed"],
                errors=[str(exc)],
            )

        # Filter strictly for PAPER_FORWARD
        forward_trades = [
            r
            for r in dataset.rows
            if str(r.get("evidence_class") or "").upper() == CLASS_PAPER_FORWARD
        ]

        if strategy_id:
            forward_trades = [
                r for r in forward_trades if str(r.get("strategy_id") or "") == strategy_id
            ]

        total_forward_trades = len(forward_trades)
        if total_forward_trades == 0:
            notes.append(
                "No genuine PAPER_FORWARD trades found in dataset; safe zero-result completion."
            )
            report = DailyLearningCycleReport(
                cycle_id=cycle_id,
                execution_date=execution_date,
                started_at=started_at,
                completed_at=utcnow().isoformat(),
                runtime_seconds=round(time.perf_counter() - start_wall, 3),
                status="NO_DATA",
                strategies_analyzed=[],
                trades_processed=0,
                new_forward_trades_count=0,
                observations_generated=0,
                hypotheses_generated=0,
                candidates_generated=0,
                recommendations_generated=0,
                recommendation_ids=[],
                drift_detected={},
                insufficient_data_strategies=[],
                notes=notes,
                errors=errors,
            )
            self._persist_cycle(report, user_id)
            return report

        # Group forward trades by strategy
        by_strategy: dict[str, list[dict[str, Any]]] = {}
        for t in forward_trades:
            s_id = str(t["strategy_id"])
            by_strategy.setdefault(s_id, []).append(t)

        all_observations_saved = 0
        all_hypotheses_generated: list[ResearchHypothesis] = []
        all_candidates_count = 0
        all_recommendations_count = 0

        # Run per-strategy learning & pattern detection
        for s_id, s_trades in by_strategy.items():
            strategies_analyzed.append(s_id)
            n_trades = len(s_trades)

            if n_trades < min_sample_size:
                insufficient_data_strategies.append(s_id)
                notes.append(
                    f"Strategy {s_id} has {n_trades} forward trades (requires min {min_sample_size}); skipping."
                )
                continue

            # 2. Performance Analysis across all declared axes
            strategy_dataset = LearningDataset(
                rows=s_trades,
                missing_features={},
                generated_at=dataset.generated_at,
            )
            analysis = PerformanceAnalysis(strategy_dataset).analyse(
                strategy=s_id,
                min_sample=min_sample_size,
            )

            # 3. Drift Analysis (Compare Backtest baseline vs Forward paper trades)
            backtest_trades = [
                r
                for r in dataset.rows
                if str(r.get("strategy_id") or "") == s_id
                and str(r.get("evidence_class") or "").upper() in ("BACKTEST", "IN_SAMPLE")
            ]
            if len(backtest_trades) >= min_sample_size and len(s_trades) >= min_sample_size:
                drift_pair = compare_sources(
                    backtest_trades,
                    s_trades,
                    reference="BACKTEST",
                    comparison="PAPER_FORWARD",
                    min_sample=min_sample_size,
                )
                has_drift = bool(drift_pair.deteriorated or drift_pair.improved)
                drift_detected[s_id] = has_drift
                if has_drift:
                    notes.append(
                        f"Drift detected for strategy {s_id}: deteriorated={drift_pair.deteriorated}, improved={drift_pair.improved}"
                    )
            else:
                drift_detected[s_id] = False

            # 4. Resolve strategy version to inspect adaptive parameters
            try:
                ver_row = self._optimization_service.get_strategy_version(s_id)
                def_raw = ver_row["definition"]
                s_def = json.loads(def_raw) if isinstance(def_raw, str) else def_raw
                if not isinstance(s_def, dict):
                    raise TypeError(f"Strategy {s_id} definition must be an object")
                adaptive_params = extract_adaptive_parameters(s_def)
                strat_version = ver_row["version"]
            except (KeyError, LookupError, TypeError, ValueError) as exc:
                notes.append(f"Strategy {s_id} version could not be resolved: {exc}")
                errors.append(f"Strategy {s_id} version could not be resolved: {exc}")
                logger.exception("Strategy version resolution failed for {}", s_id)
                continue

            # Persist genuine forward observations with strict n >= min_sample_size
            observation_inputs: list[dict[str, Any]] = []
            for breakdown in analysis.breakdowns:
                for bucket in breakdown.buckets:
                    if bucket.get("suppressed"):
                        continue
                    n = bucket.get("n", 0)
                    if n < min_sample_size:
                        continue

                    p_val = bucket.get("p_adjusted")
                    sig = bucket.get("significance")
                    # Never promote weak observations or isolated outliers.
                    settings = get_settings()
                    if sig not in ("strong", "moderate") and (
                        p_val is None or p_val > settings.learning_p_adjusted_cutoff
                    ):
                        continue

                    cond_bucket = f"{breakdown.axis}:{bucket.get('label')}"
                    source_ids = bucket_source_trade_refs(
                        s_trades,
                        breakdown.axis,
                        str(bucket["label"]),
                        analysis.metric,
                    )
                    conf = (
                        (1.0 - p_val)
                        if p_val is not None
                        else (
                            settings.learning_confidence_strong
                            if sig == "strong"
                            else settings.daily_learning_confidence_other
                        )
                    )
                    observation_inputs.append(
                        {
                            "strategy_id": s_id,
                            "strategy_version": strat_version,
                            "date": execution_date,
                            "metric": analysis.metric,
                            "condition_bucket": cond_bucket,
                            "sample_size": n,
                            "statistical_result": bucket,
                            "evidence_class": CLASS_PAPER_FORWARD,
                            "confidence": conf,
                            "source_trades": source_ids,
                        }
                    )
            strat_observations = self._storage.record_observations(observation_inputs)
            all_observations_saved += len(strat_observations)

            if not adaptive_params:
                notes.append(
                    f"Strategy {s_id} has no parameters marked adaptive; no optimization candidates generated."
                )
                continue

            # Convert observations to hypotheses
            candidates: list[OptimizationCandidate] = []

            for obs in strat_observations:
                stat_res = obs.get("statistical_result") or {}
                n = obs.get("sample_size") or 0
                bucket_stats = stat_res.get("stats") or {}
                lift = stat_res.get("lift")
                cond = obs.get("condition_bucket", "")
                axis_name, _, bucket_label = cond.partition(":")

                # Build candidate using existing optimizer generator
                cand_list = generate_candidates_from_observations(
                    s_def,
                    [
                        {
                            "axis": axis_name,
                            "sample_size": n,
                            "statement": f"{cond} lift {lift}",
                            "evidence": {
                                "axis": axis_name,
                                "bucket": bucket_label or cond,
                                "n": n,
                                "lift": lift,
                                "stats": bucket_stats,
                            },
                        }
                    ],
                    min_sample_size=min_sample_size,
                )

                for cand in cand_list:
                    candidates.append(cand)
                    hyp = ResearchHypothesis(
                        hypothesis_id=f"hyp_{uuid.uuid4().hex[:10]}",
                        strategy_id=s_id,
                        strategy_version=ver_row.get("version"),
                        parameter_target=cand.parameter_name,
                        statement=f"Strategy {s_id} may perform better when {cand.parameter_name} is set to {cand.proposed_value} (observed {cond}, lift {lift}).",
                        condition_bucket=cond,
                        sample_size=n,
                        baseline_mean=bucket_stats.get("baseline_mean"),
                        bucket_mean=bucket_stats.get("mean"),
                        lift=lift,
                        p_value=stat_res.get("p_adjusted"),
                        confidence="High" if stat_res.get("significance") == "strong" else "Medium",
                    )
                    all_hypotheses_generated.append(hyp)

            all_candidates_count += len(candidates)

            # 5. Feed into existing Controlled Optimization Layer
            if candidates:
                try:
                    recs = self._optimization_service.run_optimization(
                        s_id,
                        version=ver_row.get("version"),
                        user_id=user_id,
                        feed=feed,
                        observations=[
                            {
                                "axis": h.condition_bucket.split(":")[0],
                                "sample_size": h.sample_size,
                                "statement": h.statement,
                                "evidence": {
                                    "axis": h.condition_bucket.split(":")[0],
                                    "bucket": h.condition_bucket,
                                    "n": h.sample_size,
                                    "lift": h.lift,
                                    "stats": {"mean": h.bucket_mean, "lift": h.lift},
                                },
                            }
                            for h in all_hypotheses_generated
                            if h.strategy_id == s_id
                        ],
                        min_sample_size=min_sample_size,
                    )
                    all_recommendations_count += len(recs)
                    for r in recs:
                        recommendation_ids.append(r["recommendation_id"])
                        notes.append(
                            f"Generated recommendation {r['recommendation_id'][:8]} ({r['status']}) for {r['parameter']}."
                        )
                except Exception as exc:
                    err_msg = f"Optimization run failed for strategy {s_id}: {exc}"
                    logger.exception(err_msg)
                    errors.append(err_msg)

        # 5b. Record context observations and report failures without inventing data.
        try:
            context_observations = self._context_service.record_effectiveness_observations(
                user_id,
                strategy_id=strategy_id,
                min_sample=min_sample_size,
            )
            if context_observations:
                all_observations_saved += len(context_observations)
                notes.append(
                    f"Recorded {len(context_observations)} context-score observation(s) "
                    "from the signal context engine."
                )
        except Exception as exc:  # noqa: BLE001 — context analysis never gates the cycle
            notes.append(f"Context effectiveness analysis skipped: {exc}")
            errors.append(f"Context effectiveness analysis failed: {exc}")
            logger.warning("context effectiveness analysis skipped: {}", exc)

        status = "SUCCESS"
        if errors and not recommendation_ids and not all_observations_saved:
            status = "ERROR"
        elif errors:
            status = "PARTIAL"
        elif not all_observations_saved and insufficient_data_strategies:
            status = "INSUFFICIENT_SAMPLE"

        report = DailyLearningCycleReport(
            cycle_id=cycle_id,
            execution_date=execution_date,
            started_at=started_at,
            completed_at=utcnow().isoformat(),
            runtime_seconds=round(time.perf_counter() - start_wall, 3),
            status=status,
            strategies_analyzed=strategies_analyzed,
            trades_processed=total_forward_trades,
            new_forward_trades_count=total_forward_trades,
            observations_generated=all_observations_saved,
            hypotheses_generated=len(all_hypotheses_generated),
            candidates_generated=all_candidates_count,
            recommendations_generated=all_recommendations_count,
            recommendation_ids=recommendation_ids,
            drift_detected=drift_detected,
            insufficient_data_strategies=insufficient_data_strategies,
            notes=notes,
            errors=errors,
        )

        self._persist_cycle(report, user_id)
        return report

    def _persist_cycle(self, report: DailyLearningCycleReport, user_id: str) -> None:
        """Store a cycle report under its owning account.

        Args:
            report: Completed cycle report.
            user_id: Account whose evidence the cycle analyzed.
        """
        self._storage.save_cycle(report.to_dict(), user_id)

    def get_latest_cycle(self, user_id: str) -> dict[str, Any] | None:
        """Fetch the latest cycle for one account.

        Args:
            user_id: Account that owns the cycle.
        """
        return self._storage.latest_cycle(user_id)

    def get_cycle_history(self, user_id: str, limit: int) -> list[dict[str, Any]]:
        """Fetch recent cycles for one account.

        Args:
            user_id: Account that owns the cycles.
            limit: Maximum number of reports to return.
        """
        return self._storage.cycle_history(user_id, limit)
