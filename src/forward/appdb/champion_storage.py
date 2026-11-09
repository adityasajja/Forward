"""SQL adapter for champion and challenger comparison reads."""

from __future__ import annotations

from typing import Any

from forward.appdb.deployment_repository import DeploymentRepository
from forward.appdb.engine import AppDatabase
from forward.appdb.strategy_repository import StrategyRepository
from forward.core.portfolio_ports import (
    ChampionStoragePort,
)


class SqlChampionStorage(ChampionStoragePort):
    """Read strategy versions and paper deployments through SQL repositories."""

    def __init__(self, database: AppDatabase) -> None:
        """Create the adapter for the configured application database.

        Args:
            database: Database that owns the repository session lifecycle.
        """
        self._database = database

    def strategy_versions(self, user_id: str, strategy_id: str) -> list[dict[str, Any]]:
        """Return versions only when the account owns the strategy.

        Args:
            user_id: Account that owns the strategy.
            strategy_id: Strategy whose immutable versions are requested.

        Returns:
            Owned strategy versions, newest first.
        """
        with self._database.session() as session:
            if StrategyRepository.get(session, strategy_id, user_id) is None:
                return []
            return StrategyRepository.versions(session, strategy_id)

    def running_paper_deployments(self, user_id: str, strategy_id: str) -> list[dict[str, Any]]:
        """Return an account's running PAPER deployments for one strategy.

        Args:
            user_id: Account that owns the deployments.
            strategy_id: Strategy whose running deployments are requested.

        Returns:
            Matching deployments, newest first.
        """
        with self._database.session() as session:
            return [
                row
                for row in DeploymentRepository.list_for_user(session, user_id, status="RUNNING")
                if row.get("strategy_id") == strategy_id
                and str(row.get("mode") or "").upper() == "PAPER"
            ]
