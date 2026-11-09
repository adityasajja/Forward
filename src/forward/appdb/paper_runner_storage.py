"""SQL adapter for paper-runner deployment and strategy reads."""

from __future__ import annotations

from typing import Any

from forward.appdb.deployment_repository import DeploymentRepository
from forward.appdb.engine import AppDatabase
from forward.appdb.strategy_repository import StrategyRepository
from forward.core.paper_health_ports import PaperHealthStoragePort
from forward.core.paper_runner_storage_ports import PaperRunnerStoragePort


class SqlPaperRunnerStorage(PaperRunnerStoragePort, PaperHealthStoragePort):
    """Read deployment and exact-version data through SQL repositories."""

    def __init__(self, database: AppDatabase) -> None:
        """Create the adapter for the application's configured database.

        Args:
            database: Database that owns the SQL session lifecycle.
        """
        self._database = database

    def running_deployments(self) -> list[dict[str, Any]]:
        """Return active paper and live deployments.

        Returns:
            Deployment rows for the execution loop.
        """
        with self._database.session() as session:
            return DeploymentRepository.running_for_execution(session)

    def strategy_version(self, strategy_id: str, version: int) -> dict[str, Any] | None:
        """Return the named immutable strategy version.

        Args:
            strategy_id: Stable identifier of the strategy.
            version: Exact immutable version number.

        Returns:
            The version row, or ``None`` when it does not exist.
        """
        with self._database.session() as session:
            return StrategyRepository.version(session, strategy_id, version)

    def strategy_versions(
        self, keys: list[tuple[str, int]]
    ) -> dict[tuple[str, int], dict[str, Any]]:
        """Return all requested versions in bounded repository batches.

        Args:
            keys: Exact strategy identifiers and version numbers.

        Returns:
            A mapping keyed by strategy identifier and version number.
        """
        with self._database.session() as session:
            return StrategyRepository.versions_for_keys(session, keys)

    def health_deployments(self, user_id: str) -> list[dict[str, Any]]:
        """Return active and paused deployments with their exact definitions.

        Args:
            user_id: Account whose deployments should be checked.

        Returns:
            Owned deployments in running or paused state with their version
            definitions attached.
        """
        with self._database.session() as session:
            rows = [
                row
                for row in DeploymentRepository.list_for_user(session, user_id)
                if row.get("status") in ("RUNNING", "PAUSED")
            ]
            keys = [
                (row["strategy_id"], int(row["strategy_version"]))
                for row in rows
            ]
            versions = StrategyRepository.versions_for_keys(session, keys)
            for row in rows:
                version = versions.get(
                    (row["strategy_id"], int(row["strategy_version"]))
                )
                if version:
                    row["definition"] = version.get("definition")
        return rows
