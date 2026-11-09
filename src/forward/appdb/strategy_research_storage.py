"""SQL adapter for strategy-experiment persistence."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy.orm import Session

from forward.appdb.deployment_repository import DeploymentRepository
from forward.appdb.engine import AppDatabase
from forward.appdb.learning_observation_repository import (
    LearningObservationRepository,
)
from forward.appdb.strategy_repository import StrategyRepository
from forward.appdb.strategy_research_repository import (
    OptimizationRecommendationRepository,
    StrategyExperimentRepository,
)


class _SqlExperimentUnitOfWork:
    """Expose experiment repositories through one database session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def owns_strategy(self, strategy_id: str, user_id: str) -> bool:
        """Return whether the account owns the strategy."""
        return StrategyRepository.get(self._session, strategy_id, user_id) is not None

    def recent_deployments(self, strategy_id: str, user_id: str) -> list[dict[str, Any]]:
        """Return recent deployment settings for a strategy."""
        return DeploymentRepository.recent_for_strategy(
            self._session, strategy_id, user_id=user_id
        )

    def latest_strategy_version(
        self, strategy_id: str, user_id: str
    ) -> dict[str, Any] | None:
        """Return the newest stored version of a strategy."""
        if StrategyRepository.get(self._session, strategy_id, user_id) is None:
            return None
        return StrategyRepository.latest_version(self._session, strategy_id)

    def strategy_version(
        self, strategy_id: str, version: int, user_id: str
    ) -> dict[str, Any] | None:
        """Return one stored strategy version."""
        if StrategyRepository.get(self._session, strategy_id, user_id) is None:
            return None
        return StrategyRepository.version(self._session, strategy_id, version)

    def create_strategy_version(self, **values: Any) -> dict[str, Any]:
        """Create one immutable strategy version in the current transaction."""
        return StrategyRepository.create_version(self._session, **values)

    def strategy_version_record(
        self, strategy_id: str, version: int | None, user_id: str
    ) -> dict[str, Any] | None:
        """Return a requested strategy version or the newest version."""
        if version is None:
            return self.latest_strategy_version(strategy_id, user_id)
        return self.strategy_version(strategy_id, version, user_id)

    def recommendation(
        self, recommendation_id: str, user_id: str
    ) -> dict[str, Any] | None:
        """Return one optimization recommendation."""
        return OptimizationRecommendationRepository.get(
            self._session, recommendation_id, user_id
        )

    def create_recommendation(self, user_id: str, **values: Any) -> dict[str, Any]:
        """Insert one optimization recommendation."""
        return OptimizationRecommendationRepository.create(
            self._session, user_id=user_id, **values
        )

    def recommendations(
        self, strategy_id: str, user_id: str, status: str | None
    ) -> list[dict[str, Any]]:
        """List recommendations for a strategy."""
        return OptimizationRecommendationRepository.list_for_strategy(
            self._session, user_id, strategy_id, status=status
        )

    def update_recommendation_status(
        self, recommendation_id: str, user_id: str, status: str, **values: Any
    ) -> dict[str, Any]:
        """Update recommendation status and review fields."""
        return OptimizationRecommendationRepository.update_status(
            self._session,
            recommendation_id=recommendation_id,
            user_id=user_id,
            status=status,
            **values,
        )

    def create_experiment(self, user_id: str, **values: Any) -> dict[str, Any]:
        """Insert one experiment with its baseline and candidate definitions."""
        return StrategyExperimentRepository.create(
            self._session, user_id=user_id, **values
        )

    def experiment(self, experiment_id: str, user_id: str) -> dict[str, Any] | None:
        """Return one experiment."""
        return StrategyExperimentRepository.get(self._session, experiment_id, user_id)

    def update_experiment_status(
        self, experiment_id: str, user_id: str, status: str, **values: Any
    ) -> dict[str, Any] | None:
        """Update an experiment status and its review or error fields."""
        return StrategyExperimentRepository.update_status(
            self._session, experiment_id, user_id, status, **values
        )

    def save_experiment_results(
        self, experiment_id: str, user_id: str, **values: Any
    ) -> dict[str, Any] | None:
        """Save the completed experiment results and explanation."""
        return StrategyExperimentRepository.save_results(
            self._session, experiment_id, user_id, **values
        )

    def experiments(self, user_id: str, **filters: Any) -> list[dict[str, Any]]:
        """List experiments matching the supplied filters."""
        return StrategyExperimentRepository.list_for_strategy(
            self._session, user_id=user_id, **filters
        )

    def learning_observations(self, strategy_id: str, user_id: str) -> list[dict[str, Any]]:
        """Return learning observations for one strategy."""
        if StrategyRepository.get(self._session, strategy_id, user_id) is None:
            raise LookupError(f"strategy {strategy_id} not found")
        return LearningObservationRepository.list_observations(
            self._session, user_id=user_id, strategy_id=strategy_id
        )


class SqlStrategyResearchStorage:
    """Open SQL-backed strategy-research units of work."""

    def __init__(self, database: AppDatabase) -> None:
        """Create the adapter for the application's configured database.

        Args:
            database: Database that owns the repository session lifecycle.
        """
        self._database = database

    @contextmanager
    def unit_of_work(self) -> Iterator[_SqlExperimentUnitOfWork]:
        """Yield repositories bound to one database session.

        Yields:
            A unit of work whose repository changes share the session context.
        """
        with self._database.session() as session:
            yield _SqlExperimentUnitOfWork(session)
