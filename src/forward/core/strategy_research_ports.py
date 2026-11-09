"""Storage operations for strategy research and review."""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any, Protocol


class StrategyResearchUnitOfWorkPort(Protocol):
    """Operations available inside one strategy-research transaction."""

    def owns_strategy(self, strategy_id: str, user_id: str) -> bool:
        """Return whether the account owns the strategy."""

    def recent_deployments(self, strategy_id: str, user_id: str) -> list[dict[str, Any]]:
        """Return recent deployment settings for a strategy."""

    def latest_strategy_version(self, strategy_id: str, user_id: str) -> dict[str, Any] | None:
        """Return the newest stored version of a strategy."""

    def strategy_version(
        self, strategy_id: str, version: int, user_id: str
    ) -> dict[str, Any] | None:
        """Return one stored strategy version."""

    def strategy_version_record(
        self, strategy_id: str, version: int | None, user_id: str
    ) -> dict[str, Any] | None:
        """Return a requested strategy version or the newest version."""

    def create_strategy_version(self, **values: Any) -> dict[str, Any]:
        """Create one immutable strategy version in this transaction."""

    def recommendation(self, recommendation_id: str, user_id: str) -> dict[str, Any] | None:
        """Return one optimization recommendation."""

    def create_recommendation(self, user_id: str, **values: Any) -> dict[str, Any]:
        """Insert one optimization recommendation."""

    def recommendations(
        self, strategy_id: str, user_id: str, status: str | None
    ) -> list[dict[str, Any]]:
        """List recommendations for a strategy."""

    def update_recommendation_status(
        self, recommendation_id: str, user_id: str, status: str, **values: Any
    ) -> dict[str, Any]:
        """Update recommendation status and review fields."""

    def create_experiment(self, user_id: str, **values: Any) -> dict[str, Any]:
        """Insert one experiment with its baseline and candidate definitions."""

    def experiment(self, experiment_id: str, user_id: str) -> dict[str, Any] | None:
        """Return one experiment."""

    def update_experiment_status(
        self, experiment_id: str, user_id: str, status: str, **values: Any
    ) -> dict[str, Any] | None:
        """Update an experiment status and its review or error fields."""

    def save_experiment_results(
        self, experiment_id: str, user_id: str, **values: Any
    ) -> dict[str, Any] | None:
        """Save the completed experiment results and explanation."""

    def experiments(self, user_id: str, **filters: Any) -> list[dict[str, Any]]:
        """List experiments matching the supplied filters."""

    def learning_observations(
        self, strategy_id: str, user_id: str
    ) -> list[dict[str, Any]]:
        """Return learning observations for one strategy."""


class StrategyResearchStoragePort(Protocol):
    """Open database transactions for strategy research use cases."""

    def unit_of_work(self) -> AbstractContextManager[StrategyResearchUnitOfWorkPort]:
        """Open one unit of work whose changes commit together."""
