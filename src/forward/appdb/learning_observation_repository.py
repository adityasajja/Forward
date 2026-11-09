"""SQL repository for learning observations."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session

from forward.appdb.engine import utcnow
from forward.appdb.repository_support import decode_json_fields, many, new_id
from forward.appdb.research_schema import learning_observations
from forward.appdb.strategy_backtest_schema import strategies


class LearningObservationRepository:
    """Persistent observations & research findings extracted from forward trades."""

    @staticmethod
    def record(
        session: Session,
        *,
        strategy_id: str,
        strategy_version: int | None,
        date: str,
        metric: str,
        condition_bucket: str,
        sample_size: int,
        statistical_result: dict[str, Any],
        evidence_class: str,
        confidence: float | None = None,
        source_trades: list[str] | None = None,
    ) -> dict[str, Any]:
        obs_id = new_id()
        now = utcnow()
        row = {
            "observation_id": obs_id,
            "strategy_id": strategy_id,
            "strategy_version": strategy_version,
            "date": date,
            "created_at": now,
            "metric": metric,
            "condition_bucket": condition_bucket,
            "sample_size": int(sample_size),
            "statistical_result": json.dumps(statistical_result, default=str),
            "evidence_class": evidence_class,
            "confidence": float(confidence) if confidence is not None else None,
            "source_trades": json.dumps(source_trades or []),
        }
        session.execute(insert(learning_observations).values(**row))
        return {
            **row,
            "statistical_result": statistical_result,
            "source_trades": source_trades or [],
        }

    @staticmethod
    def list_observations(
        session: Session,
        *,
        user_id: str | None = None,
        strategy_id: str | None = None,
        strategy_version: int | None = None,
        evidence_class: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        stmt = (
            select(learning_observations)
            .order_by(learning_observations.c.created_at.desc())
        )
        if user_id is not None:
            owned_observations = learning_observations.join(
                strategies, learning_observations.c.strategy_id == strategies.c.strategy_id
            )
            stmt = stmt.select_from(owned_observations).where(strategies.c.user_id == user_id)
        if strategy_id:
            stmt = stmt.where(learning_observations.c.strategy_id == strategy_id)
        if strategy_version is not None:
            stmt = stmt.where(learning_observations.c.strategy_version == strategy_version)
        if evidence_class:
            stmt = stmt.where(learning_observations.c.evidence_class == evidence_class)
        stmt = stmt.limit(max(1, min(limit, 500)))
        rows = many(session.execute(stmt).all())
        return [decode_json_fields(row, ("statistical_result", "source_trades")) for row in rows]

    @staticmethod
    def observation_counts_by_strategy(
        session: Session, evidence_classes: tuple[str, ...], user_id: str | None = None
    ) -> dict[str, int]:
        """Count all matching observations by strategy without a row limit.

        Args:
            session: Active database session.
            evidence_classes: Evidence classes whose observations are counted.

        Returns:
            Complete counts keyed by strategy ID.
        """
        if not evidence_classes:
            return {}
        statement = (
            select(
                learning_observations.c.strategy_id,
                func.count().label("observation_count"),
            )
            .where(learning_observations.c.evidence_class.in_(evidence_classes))
            .group_by(learning_observations.c.strategy_id)
        )
        if user_id is not None:
            owned_observations = learning_observations.join(
                strategies, learning_observations.c.strategy_id == strategies.c.strategy_id
            )
            statement = statement.select_from(owned_observations).where(
                strategies.c.user_id == user_id
            )
        return {
            str(strategy_id): int(count)
            for strategy_id, count in session.execute(statement).all()
        }
