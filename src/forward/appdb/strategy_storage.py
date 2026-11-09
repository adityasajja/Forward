"""SQL adapter for strategy authoring and version history."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy.orm import Session

from forward.appdb.engine import AppDatabase
from forward.appdb.strategy_repository import StrategyRepository
from forward.core.strategy_authoring_ports import (
    StrategyAuthoringUnitOfWorkPort,
)


class _SqlStrategyAuthoringUnitOfWork:
    """Run strategy repository operations inside one SQL session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def name_taken(self, user_id: str, name: str) -> bool:
        """Check whether an account already uses a strategy name."""
        return StrategyRepository.name_taken(self._session, user_id, name)

    def create(self, **values: Any) -> dict[str, Any]:
        """Create one strategy without a version."""
        return StrategyRepository.create(self._session, **values)

    def list_for_user(
        self, user_id: str, *, include_archived: bool
    ) -> list[dict[str, Any]]:
        """List strategies owned by an account."""
        return StrategyRepository.list_for_user(
            self._session,
            user_id,
            include_archived=include_archived,
            include_version_summary=False,
        )

    def version_summaries(
        self, strategy_ids: list[str]
    ) -> dict[str, tuple[dict[str, Any], int]]:
        """Return each strategy's latest version and version count."""
        return StrategyRepository.version_summaries(self._session, strategy_ids)

    def get(self, strategy_id: str, user_id: str) -> dict[str, Any] | None:
        """Read one strategy owned by an account."""
        return StrategyRepository.get(self._session, strategy_id, user_id)

    def delete_history(self, strategy_id: str, version: int | None) -> None:
        """Delete runs and records created by a strategy version."""
        StrategyRepository.delete_history(self._session, strategy_id, version)

    def delete(self, strategy_id: str) -> int:
        """Delete one strategy row."""
        return StrategyRepository.delete(self._session, strategy_id)

    def version(self, strategy_id: str, version: int) -> dict[str, Any] | None:
        """Read one exact strategy version."""
        return StrategyRepository.version(self._session, strategy_id, version)

    def delete_version(self, strategy_id: str, version: int) -> int:
        """Delete one exact strategy version."""
        return StrategyRepository.delete_version(self._session, strategy_id, version)

    def active_deployment_count(
        self, user_id: str, strategy_id: str, version: int | None
    ) -> int:
        """Count active deployments using a strategy or version."""
        return StrategyRepository.active_deployment_count(
            self._session, user_id, strategy_id, version
        )

    def versions(self, strategy_id: str) -> list[dict[str, Any]]:
        """List a strategy's versions, newest first."""
        return StrategyRepository.versions(self._session, strategy_id)

    def create_version(self, **values: Any) -> dict[str, Any]:
        """Append one validated immutable version."""
        return StrategyRepository.create_version(self._session, **values)

    def latest_version(self, strategy_id: str) -> dict[str, Any] | None:
        """Read the newest version of a strategy."""
        return StrategyRepository.latest_version(self._session, strategy_id)

    def version_count(self, strategy_id: str) -> int:
        """Count versions belonging to a strategy."""
        return StrategyRepository.version_count(self._session, strategy_id)


class SqlStrategyAuthoringStorage:
    """Open SQL-backed strategy-authoring units of work."""

    def __init__(self, database: AppDatabase) -> None:
        """Create the adapter for the application's configured database.

        Args:
            database: Database that owns the repository session lifecycle.
        """
        self._database = database

    @contextmanager
    def unit_of_work(self) -> Iterator[StrategyAuthoringUnitOfWorkPort]:
        """Yield repository operations bound to one database session.

        Yields:
            Operations whose changes share the session transaction.
        """
        with self._database.session() as session:
            yield _SqlStrategyAuthoringUnitOfWork(session)
