"""Ports for the related application workflows."""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any, Protocol


class StrategyAuthoringUnitOfWorkPort(Protocol):
    """Operations available inside one strategy-authoring transaction."""

    def name_taken(self, user_id: str, name: str) -> bool:
        """Check whether an account already uses a strategy name."""

    def create(self, **values: Any) -> dict[str, Any]:
        """Create one strategy without a version."""

    def list_for_user(self, user_id: str, *, include_archived: bool) -> list[dict[str, Any]]:
        """List strategies owned by an account."""

    def version_summaries(self, strategy_ids: list[str]) -> dict[str, tuple[dict[str, Any], int]]:
        """Return each strategy's latest version and version count."""

    def get(self, strategy_id: str, user_id: str) -> dict[str, Any] | None:
        """Read one strategy owned by an account."""

    def delete_history(self, strategy_id: str, version: int | None) -> None:
        """Delete runs and records created by a strategy version."""

    def delete(self, strategy_id: str) -> int:
        """Delete one strategy row."""

    def version(self, strategy_id: str, version: int) -> dict[str, Any] | None:
        """Read one exact strategy version."""

    def delete_version(self, strategy_id: str, version: int) -> int:
        """Delete one exact strategy version."""

    def active_deployment_count(self, user_id: str, strategy_id: str, version: int | None) -> int:
        """Count active deployments using a strategy or version."""

    def versions(self, strategy_id: str) -> list[dict[str, Any]]:
        """List a strategy's versions, newest first."""

    def create_version(self, **values: Any) -> dict[str, Any]:
        """Append one validated immutable version."""

    def latest_version(self, strategy_id: str) -> dict[str, Any] | None:
        """Read the newest version of a strategy."""

    def version_count(self, strategy_id: str) -> int:
        """Count versions belonging to a strategy."""


class StrategyAuthoringStoragePort(Protocol):
    """Open transactions for strategy-authoring use cases."""

    def unit_of_work(self) -> AbstractContextManager[StrategyAuthoringUnitOfWorkPort]:
        """Open a unit of work whose changes commit together."""
