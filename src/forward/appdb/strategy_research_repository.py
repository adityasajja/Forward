"""SQL repositories for optimization recommendations and experiments."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from forward.appdb.engine import utcnow
from forward.appdb.repository_support import decode_json_fields, many, new_id, one
from forward.appdb.research_schema import optimization_recommendations, strategy_experiments
from forward.appdb.strategy_backtest_schema import strategies, strategy_versions

_RECOMMENDATION_JSON_FIELDS = (
    "source_observations",
    "baseline_metrics",
    "candidate_metrics",
    "walk_forward_metrics",
    "robustness_results",
)
_EXPERIMENT_JSON_FIELDS = (
    "parameter_changes",
    "baseline_definition",
    "candidate_definition",
    "results",
    "explanation",
)


def _owned_strategy_ids(user_id: str) -> Any:
    """Return the strategy IDs visible to one account."""
    return select(strategies.c.strategy_id).where(strategies.c.user_id == user_id)


def _require_strategy_owner(session: Session, strategy_id: str, user_id: str) -> None:
    """Reject research writes for a strategy the account does not own."""
    owner = session.execute(
        select(strategies.c.strategy_id).where(
            strategies.c.strategy_id == strategy_id,
            strategies.c.user_id == user_id,
        )
    ).scalar_one_or_none()
    if owner is None:
        raise LookupError(f"strategy {strategy_id} not found")


class OptimizationRecommendationRepository:
    """Persistent optimization and parameter adaptation recommendations."""

    @staticmethod
    def create(
        session: Session,
        *,
        user_id: str,
        strategy_id: str,
        source_strategy_version: int,
        parameter: str,
        current_value: float,
        proposed_value: float,
        reason: str,
        source_observations: list[dict[str, Any]] | str,
        sample_size: int,
        baseline_metrics: dict[str, Any],
        candidate_metrics: dict[str, Any],
        walk_forward_metrics: dict[str, Any],
        robustness_results: dict[str, Any],
        confidence: str | float | None = None,
        status: str = "PROPOSED",
        recommendation_id: str | None = None,
    ) -> dict[str, Any]:
        _require_strategy_owner(session, strategy_id, user_id)
        version_exists = session.execute(
            select(strategy_versions.c.version).where(
                strategy_versions.c.strategy_id == strategy_id,
                strategy_versions.c.version == source_strategy_version,
            )
        ).scalar_one_or_none()
        if version_exists is None:
            raise LookupError(f"strategy {strategy_id} version {source_strategy_version} not found")
        rec_id = recommendation_id or new_id()
        now = utcnow()
        row = {
            "recommendation_id": rec_id,
            "strategy_id": strategy_id,
            "source_strategy_version": int(source_strategy_version),
            "target_strategy_version": None,
            "parameter": parameter,
            "current_value": float(current_value),
            "proposed_value": float(proposed_value),
            "reason": reason,
            "source_observations": (
                json.dumps(source_observations, default=str)
                if not isinstance(source_observations, str)
                else source_observations
            ),
            "sample_size": int(sample_size),
            "baseline_metrics": json.dumps(baseline_metrics, default=str),
            "candidate_metrics": json.dumps(candidate_metrics, default=str),
            "walk_forward_metrics": json.dumps(walk_forward_metrics, default=str),
            "robustness_results": json.dumps(robustness_results, default=str),
            "confidence": str(confidence) if confidence is not None else None,
            "status": status,
            "rejection_reason": None,
            "created_at": now,
            "updated_at": now,
            "reviewed_by": None,
            "reviewed_at": None,
        }
        session.execute(insert(optimization_recommendations).values(**row))
        return decode_json_fields(row, _RECOMMENDATION_JSON_FIELDS)

    @staticmethod
    def get(
        session: Session, recommendation_id: str, user_id: str
    ) -> dict[str, Any] | None:
        stmt = (
            select(optimization_recommendations)
            .join(
                strategies,
                strategies.c.strategy_id == optimization_recommendations.c.strategy_id,
            )
            .where(
                optimization_recommendations.c.recommendation_id == recommendation_id,
                strategies.c.user_id == user_id,
            )
        )
        row = one(session.execute(stmt).first())
        return decode_json_fields(row, _RECOMMENDATION_JSON_FIELDS) if row else None

    @staticmethod
    def list_for_strategy(
        session: Session,
        user_id: str,
        strategy_id: str | None = None,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        stmt = (
            select(optimization_recommendations)
            .join(
                strategies,
                strategies.c.strategy_id == optimization_recommendations.c.strategy_id,
            )
            .where(strategies.c.user_id == user_id)
            .order_by(optimization_recommendations.c.created_at.desc())
        )
        if strategy_id:
            stmt = stmt.where(optimization_recommendations.c.strategy_id == strategy_id)
        if status:
            stmt = stmt.where(optimization_recommendations.c.status == status)
        stmt = stmt.limit(max(1, min(limit, 500)))
        rows = many(session.execute(stmt).all())
        return [decode_json_fields(row, _RECOMMENDATION_JSON_FIELDS) for row in rows]

    @staticmethod
    def update_status(
        session: Session,
        recommendation_id: str,
        user_id: str,
        status: str,
        *,
        reviewed_by: str | None = None,
        rejection_reason: str | None = None,
        target_strategy_version: int | None = None,
    ) -> dict[str, Any] | None:
        now = utcnow()
        values: dict[str, Any] = {
            "status": status,
            "updated_at": now,
        }
        if reviewed_by:
            values["reviewed_by"] = reviewed_by
            values["reviewed_at"] = now
        if rejection_reason is not None:
            values["rejection_reason"] = rejection_reason
        if target_strategy_version is not None:
            values["target_strategy_version"] = target_strategy_version

        session.execute(
            update(optimization_recommendations)
            .where(
                optimization_recommendations.c.recommendation_id == recommendation_id,
                optimization_recommendations.c.strategy_id.in_(_owned_strategy_ids(user_id)),
            )
            .values(**values)
        )
        return OptimizationRecommendationRepository.get(session, recommendation_id, user_id)


class StrategyExperimentRepository:
    """Strategy Experiment Lab repository for comparative baseline vs candidate evaluations."""

    @staticmethod
    def create(
        session: Session,
        *,
        user_id: str,
        strategy_id: str,
        source_version: int,
        creator_user_id: str,
        name: str,
        reason: str,
        parameter_changes: dict[str, Any],
        baseline_definition: dict[str, Any],
        candidate_definition: dict[str, Any],
        recommendation_id: str | None = None,
        experiment_id: str | None = None,
        status: str = "CREATED",
    ) -> dict[str, Any]:
        _require_strategy_owner(session, strategy_id, user_id)
        version_exists = session.execute(
            select(strategy_versions.c.version).where(
                strategy_versions.c.strategy_id == strategy_id,
                strategy_versions.c.version == source_version,
            )
        ).scalar_one_or_none()
        if version_exists is None:
            raise LookupError(f"strategy {strategy_id} version {source_version} not found")
        if recommendation_id is not None:
            recommendation = OptimizationRecommendationRepository.get(
                session, recommendation_id, user_id
            )
            if recommendation is None or recommendation["strategy_id"] != strategy_id:
                raise LookupError(f"recommendation {recommendation_id} not found")
        exp_id = experiment_id or new_id()
        now = utcnow()
        row = {
            "experiment_id": exp_id,
            "strategy_id": strategy_id,
            "source_version": int(source_version),
            "target_version": None,
            "recommendation_id": recommendation_id,
            "creator_user_id": creator_user_id,
            "name": name,
            "reason": reason,
            "parameter_changes": json.dumps(parameter_changes, default=str),
            "baseline_definition": json.dumps(baseline_definition, default=str),
            "candidate_definition": json.dumps(candidate_definition, default=str),
            "status": status,
            "rejection_reason": None,
            "results": None,
            "explanation": None,
            "error": None,
            "created_at": now,
            "updated_at": now,
            "reviewed_by": None,
            "reviewed_at": None,
        }
        session.execute(insert(strategy_experiments).values(**row))
        return decode_json_fields(row, _EXPERIMENT_JSON_FIELDS)

    @staticmethod
    def get(session: Session, experiment_id: str, user_id: str) -> dict[str, Any] | None:
        stmt = (
            select(strategy_experiments)
            .join(strategies, strategies.c.strategy_id == strategy_experiments.c.strategy_id)
            .where(
                strategy_experiments.c.experiment_id == experiment_id,
                strategies.c.user_id == user_id,
            )
        )
        row = one(session.execute(stmt).first())
        return decode_json_fields(row, _EXPERIMENT_JSON_FIELDS) if row else None

    @staticmethod
    def list_for_strategy(
        session: Session,
        user_id: str,
        strategy_id: str | None = None,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        stmt = (
            select(strategy_experiments)
            .join(strategies, strategies.c.strategy_id == strategy_experiments.c.strategy_id)
            .where(strategies.c.user_id == user_id)
            .order_by(strategy_experiments.c.created_at.desc())
        )
        if strategy_id:
            stmt = stmt.where(strategy_experiments.c.strategy_id == strategy_id)
        if status:
            stmt = stmt.where(strategy_experiments.c.status == status)
        stmt = stmt.limit(max(1, min(limit, 500)))
        rows = many(session.execute(stmt).all())
        return [decode_json_fields(row, _EXPERIMENT_JSON_FIELDS) for row in rows]

    @staticmethod
    def update_status(
        session: Session,
        experiment_id: str,
        user_id: str,
        status: str,
        *,
        reviewed_by: str | None = None,
        rejection_reason: str | None = None,
        error: str | None = None,
        target_version: int | None = None,
    ) -> dict[str, Any] | None:
        now = utcnow()
        values: dict[str, Any] = {
            "status": status,
            "updated_at": now,
        }
        if reviewed_by:
            values["reviewed_by"] = reviewed_by
            values["reviewed_at"] = now
        if rejection_reason is not None:
            values["rejection_reason"] = rejection_reason
        if error is not None:
            values["error"] = error
        if target_version is not None:
            values["target_version"] = target_version

        session.execute(
            update(strategy_experiments)
            .where(
                strategy_experiments.c.experiment_id == experiment_id,
                strategy_experiments.c.strategy_id.in_(_owned_strategy_ids(user_id)),
            )
            .values(**values)
        )
        return StrategyExperimentRepository.get(session, experiment_id, user_id)

    @staticmethod
    def save_results(
        session: Session,
        experiment_id: str,
        user_id: str,
        *,
        results: dict[str, Any],
        explanation: dict[str, Any],
        status: str = "COMPLETED",
    ) -> dict[str, Any] | None:
        now = utcnow()
        session.execute(
            update(strategy_experiments)
            .where(
                strategy_experiments.c.experiment_id == experiment_id,
                strategy_experiments.c.strategy_id.in_(_owned_strategy_ids(user_id)),
            )
            .values(
                status=status,
                results=json.dumps(results, default=str),
                explanation=json.dumps(explanation, default=str),
                updated_at=now,
            )
        )
        return StrategyExperimentRepository.get(session, experiment_id, user_id)
