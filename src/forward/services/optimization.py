"""Optimization service: controls strategy parameter adaptation and recommendation lifecycle.

CRITICAL SAFETY BOUNDARIES:
- Never allow automatic live strategy modification or autonomous deployment.
- Only parameters explicitly marked as adaptive may be optimized.
- Baseline strategy version V_N is strictly immutable; applying an approved recommendation
  produces V_{N+1}.
- Multi-metric evaluation and walk-forward out-of-sample gating are enforced.
- Robustness sensitivity scan ensures stable parameter plateaus over isolated spikes.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from loguru import logger

from forward.core.backtest_ports import (
    BacktestFeedPort,
    HistoricalFeedPort,
)
from forward.core.strategy_research_ports import (
    StrategyResearchStoragePort,
)
from forward.optimization.adaptive import (
    apply_parameter_to_definition,
    extract_adaptive_parameters,
)
from forward.optimization.candidates import (
    OptimizationCandidate,
    generate_candidates_from_observations,
)
from forward.optimization.evaluator import (
    evaluate_candidate,
)
from forward.services.learning import LearningService


class OptimizationService:
    """Service governing strategy parameter adaptation, evaluation, and recommendation."""

    def __init__(
        self,
        storage: StrategyResearchStoragePort,
        learning_service: LearningService,
        historical_feed: HistoricalFeedPort | None = None,
    ) -> None:
        self._storage = storage
        self._learning_service = learning_service
        self._historical_feed = historical_feed

    @property
    def learning_service(self) -> LearningService:
        return self._learning_service

    def get_strategy_version(
        self, strategy_id: str, user_id: str, version: int | None = None
    ) -> dict[str, Any]:
        """Fetch a specific strategy version or the latest version."""
        with self._storage.unit_of_work() as work:
            row = work.strategy_version_record(strategy_id, version, user_id)
            if row is None:
                raise LookupError(
                    f"Strategy {strategy_id} version {version or 'latest'} not found"
                )
            return dict(row)

    @staticmethod
    def _definition(version_row: dict[str, Any]) -> dict[str, Any]:
        """Return a strategy definition or explain why it cannot be read.

        Args:
            version_row: Stored strategy version returned by the repository.

        Returns:
            The decoded strategy definition.

        Raises:
            ValueError: The stored definition is missing or invalid JSON.
        """
        definition = version_row.get("definition")
        if isinstance(definition, str):
            definition = json.loads(definition)
        if not isinstance(definition, dict):
            raise ValueError("strategy version must contain a JSON object definition")
        return definition

    def discover_adaptive_parameters(
        self, strategy_id: str, user_id: str, version: int | None = None
    ) -> dict[str, Any]:
        """Extract all adaptive parameters explicitly declared on this strategy version."""
        ver_row = self.get_strategy_version(strategy_id, user_id, version)
        definition = self._definition(ver_row)
        params = extract_adaptive_parameters(definition)
        return {
            "strategy_id": strategy_id,
            "version": ver_row.get("version"),
            "adaptive_parameters": {k: p.to_dict() for k, p in params.items()},
            "count": len(params),
        }

    def get_forward_observations(
        self, strategy_id: str, user_id: str, *, min_sample: int = 10
    ) -> list[dict[str, Any]]:
        """Retrieve genuine forward-learning observations for this strategy."""
        with self._storage.unit_of_work() as work:
            if not work.owns_strategy(strategy_id, user_id):
                raise LookupError(f"Strategy {strategy_id} not found")
        report = self.learning_service.daily_report(refresh=False, user_id=user_id)
        observations = report.observations or []
        return [
            observation
            for observation in observations
            if (
                observation.get("sample_size")
                or (
                    observation.get("evidence", {}).get("n")
                    if isinstance(observation.get("evidence"), dict)
                    else 0
                )
                or 0
            )
            >= min_sample
        ]

    def generate_candidates(
        self,
        strategy_id: str,
        user_id: str,
        version: int | None = None,
        *,
        observations: list[dict[str, Any]] | None = None,
        min_sample_size: int = 10,
    ) -> list[OptimizationCandidate]:
        """Generate candidate parameter changes from forward learning observations."""
        ver_row = self.get_strategy_version(strategy_id, user_id, version)
        definition = self._definition(ver_row)
        if observations is None:
            observations = self.get_forward_observations(
                strategy_id, user_id, min_sample=min_sample_size
            )

        return generate_candidates_from_observations(
            definition,
            observations,
            min_sample_size=min_sample_size,
        )

    def _feed_for_strategy(
        self, definition: dict[str, Any]
    ) -> BacktestFeedPort:
        """Load real history for an explicitly configured strategy universe."""
        if self._historical_feed is None:
            raise RuntimeError("historical feed adapter is not configured")
        symbols = definition.get("symbols") or definition.get("universe_symbols")
        universe = None if symbols else definition.get("universe")
        if not symbols and not universe:
            raise ValueError("strategy must declare symbols or a named universe for historical data")
        return self._historical_feed.load(
            exchange=definition.get("exchange"),
            symbols=symbols,
            universe=universe,
        )

    def run_optimization(
        self,
        strategy_id: str,
        version: int | None = None,
        *,
        user_id: str,
        feed: BacktestFeedPort | None = None,
        observations: list[dict[str, Any]] | None = None,
        min_sample_size: int = 10,
    ) -> list[dict[str, Any]]:
        """Execute full optimization workflow:

        Forward Evidence → Hypothesis → Candidate Generation → Backtest →
        Walk-Forward Validation → Robustness Check → Recommendation.
        """
        ver_row = self.get_strategy_version(strategy_id, user_id, version)
        current_version = ver_row.get("version")
        definition = self._definition(ver_row)
        adaptive_params = extract_adaptive_parameters(definition)
        if not adaptive_params:
            logger.info("No parameters marked adaptive for strategy {}", strategy_id)
            return []

        candidates = self.generate_candidates(
            strategy_id,
            user_id,
            version=current_version,
            observations=observations,
            min_sample_size=min_sample_size,
        )
        if not candidates:
            logger.info("No eligible candidates generated from forward evidence for strategy {}", strategy_id)
            return []

        eval_feed = feed if feed is not None else self._feed_for_strategy(definition)
        recommendations: list[dict[str, Any]] = []

        for cand in candidates:
            param_spec = adaptive_params.get(cand.parameter_name)
            if param_spec is None:
                continue

            report = evaluate_candidate(eval_feed, cand, param_spec)

            rec_id = str(uuid.uuid4())
            rec_data = {
                "recommendation_id": rec_id,
                "strategy_id": strategy_id,
                "source_strategy_version": current_version,
                "parameter": cand.parameter_name,
                "current_value": cand.current_value,
                "proposed_value": cand.proposed_value,
                "reason": report.reason,
                "source_observations": cand.source_observation,
                "sample_size": cand.sample_size,
                "baseline_metrics": report.baseline_metrics.to_dict(),
                "candidate_metrics": report.candidate_metrics.to_dict(),
                "walk_forward_metrics": report.walk_forward_metrics.to_dict(),
                "robustness_results": report.robustness_result.to_dict(),
                "confidence": report.confidence,
                "status": report.status,  # RECOMMENDED or REJECTED
            }

            with self._storage.unit_of_work() as work:
                work.create_recommendation(user_id, **rec_data)

            recommendations.append(rec_data)

        return recommendations

    def list_recommendations(
        self, strategy_id: str, user_id: str, status: str | None = None
    ) -> list[dict[str, Any]]:
        """List stored optimization recommendations for a strategy."""
        with self._storage.unit_of_work() as work:
            if not work.owns_strategy(strategy_id, user_id):
                raise LookupError(f"Strategy {strategy_id} not found")
            return work.recommendations(strategy_id, user_id, status)

    def get_recommendation(
        self, recommendation_id: str, user_id: str
    ) -> dict[str, Any] | None:
        """Get an optimization recommendation by ID."""
        with self._storage.unit_of_work() as work:
            return work.recommendation(recommendation_id, user_id)

    def approve_recommendation(
        self, recommendation_id: str, user_id: str
    ) -> dict[str, Any]:
        """User explicitly approves an optimization recommendation.

        Transitions status to APPROVED. Does NOT mutate live deployments or orders.
        """
        with self._storage.unit_of_work() as work:
            rec = work.recommendation(recommendation_id, user_id)
            if rec is None:
                raise LookupError(f"Recommendation {recommendation_id} not found")
            if rec["status"] not in ("PROPOSED", "VALIDATING", "RECOMMENDED"):
                raise ValueError(
                    f"Cannot approve recommendation with current status {rec['status']}"
                )

            updated = work.update_recommendation_status(
                recommendation_id, user_id, "APPROVED", reviewed_by=user_id
            )
            if updated is None:
                raise LookupError(f"Recommendation {recommendation_id} not found")
            return updated

    def reject_recommendation(
        self, recommendation_id: str, user_id: str, reason: str | None = None
    ) -> dict[str, Any]:
        """User explicitly rejects an optimization recommendation."""
        with self._storage.unit_of_work() as work:
            rec = work.recommendation(recommendation_id, user_id)
            if rec is None:
                raise LookupError(f"Recommendation {recommendation_id} not found")

            updated = work.update_recommendation_status(
                recommendation_id,
                user_id,
                "REJECTED",
                reviewed_by=user_id,
                rejection_reason=reason,
            )
            if updated is None:
                raise LookupError(f"Recommendation {recommendation_id} not found")
            return updated

    def apply_recommendation(
        self, recommendation_id: str, author_user_id: str
    ) -> dict[str, Any]:
        """Apply an approved recommendation as a new immutable version.

        Args:
            recommendation_id: Approved recommendation to apply.
            author_user_id: Account that authors the new version.

        Returns:
            The updated recommendation and the new strategy version.

        Raises:
            LookupError: The recommendation or its source version is missing.
            ValueError: The recommendation has not been approved.
        """
        with self._storage.unit_of_work() as work:
            rec = work.recommendation(recommendation_id, author_user_id)
            if rec is None:
                raise LookupError(f"Recommendation {recommendation_id} not found")
            if rec["status"] != "APPROVED":
                raise ValueError(
                    f"Recommendation {recommendation_id} has status '{rec['status']}'; "
                    "only APPROVED recommendations may be applied to produce a new version"
                )

            source_version = work.strategy_version(
                rec["strategy_id"], rec["source_strategy_version"], author_user_id
            )
            if source_version is None:
                raise LookupError(
                    f"no source strategy version {rec['strategy_id']} "
                    f"v{rec['source_strategy_version']}"
                )
            definition = self._definition(source_version)
            adaptive_param = extract_adaptive_parameters(definition).get(rec["parameter"])
            if adaptive_param is None:
                raise ValueError(
                    f"Recommendation {recommendation_id} targets a parameter that is not adaptive"
                )
            new_definition = apply_parameter_to_definition(
                definition,
                rec["parameter"],
                rec["proposed_value"],
                block=adaptive_param.block,
            )
            change_note = (
                f"Optimized {rec['parameter']}: {rec['current_value']} -> "
                f"{rec['proposed_value']} based on recommendation "
                f"{recommendation_id} ({rec['reason']})"
            )
            new_version = work.create_strategy_version(
                strategy_id=rec["strategy_id"],
                author_user_id=author_user_id,
                definition=new_definition,
                change_note=change_note,
            )
            updated = work.update_recommendation_status(
                recommendation_id,
                author_user_id,
                "APPLIED",
                reviewed_by=author_user_id,
                target_strategy_version=new_version["version"],
            )
            if updated is None:
                raise LookupError(f"Recommendation {recommendation_id} not found")
            return {"recommendation": updated, "new_version": new_version}
