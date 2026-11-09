"""SQL adapter for paper deployment records."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy.orm import Session

from forward.appdb.deployment_repository import DeploymentRepository
from forward.appdb.engine import AppDatabase
from forward.appdb.order_repository import OrderRepository
from forward.appdb.strategy_repository import StrategyRepository
from forward.core.paper_deployment_ports import (
    PaperDeploymentStoragePort,
    PaperDeploymentUnitOfWorkPort,
)


class SqlPaperDeploymentUnitOfWork(PaperDeploymentUnitOfWorkPort):
    """Read strategy ownership and create deployment data in one SQL session."""

    def __init__(self, session: Session) -> None:
        """Create a unit of work for one database transaction.

        Args:
            session: SQL session owned by the enclosing storage context.
        """
        self._session = session

    def has_saved_strategy(self, user_id: str, strategy_id: str) -> bool:
        """Return whether the account owns the strategy.

        Args:
            user_id: Account that owns the strategy.
            strategy_id: Stable strategy identifier.

        Returns:
            Whether the strategy belongs to the account.
        """
        return StrategyRepository.get(self._session, strategy_id, user_id) is not None

    def strategy_version(self, strategy_id: str, version: int) -> dict[str, Any] | None:
        """Read one exact saved version.

        Args:
            strategy_id: Stable strategy identifier.
            version: Immutable strategy version.

        Returns:
            The stored version, or ``None`` when it does not exist.
        """
        return StrategyRepository.version(self._session, strategy_id, version)

    def create_deployment(
        self,
        *,
        user_id: str,
        strategy_id: str,
        strategy_version: int,
        mode: str,
        capital: float,
        broker_account: str | None,
        config: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Create one deployment row.

        Args:
            user_id: Account that owns the deployment.
            strategy_id: Strategy to run.
            strategy_version: Exact immutable version to run.
            mode: Execution mode.
            capital: Capital assigned to this deployment.
            broker_account: Optional broker account name.
            config: Saved deployment settings.

        Returns:
            The created deployment record.
        """
        return DeploymentRepository.create(
            self._session,
            user_id=user_id,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            mode=mode,
            capital=capital,
            broker_account=broker_account,
            config=config,
        )


class SqlPaperDeploymentStorage(PaperDeploymentStoragePort):
    """Read and update deployment records with the application database."""

    def __init__(self, database: AppDatabase) -> None:
        """Create the adapter.

        Args:
            database: Database that owns the deployment records.
        """
        self._database = database

    @contextmanager
    def unit_of_work(self) -> Iterator[PaperDeploymentUnitOfWorkPort]:
        """Open one transaction for strategy checks and deployment creation.

        Yields:
            A unit of work that reads saved strategies and creates deployments.
        """
        with self._database.session() as session:
            yield SqlPaperDeploymentUnitOfWork(session)

    def deployments(self, user_id: str, status: str | None) -> list[dict[str, Any]]:
        """Read an account's deployments.

        Args:
            user_id: Account whose deployments are requested.
            status: Optional exact status filter.

        Returns:
            Deployments ordered by their creation time.
        """
        with self._database.session() as session:
            return DeploymentRepository.list_for_user(session, user_id, status=status)

    def running_deployments(self, user_id: str) -> list[dict[str, Any]]:
        """Read the account's running deployments.

        Args:
            user_id: Account whose running deployments are requested.

        Returns:
            Running deployments ordered by their creation time.
        """
        with self._database.session() as session:
            return DeploymentRepository.list_for_user(session, user_id, status="RUNNING")

    def deployment(self, user_id: str, deployment_id: str) -> dict[str, Any] | None:
        """Read one deployment owned by the account.

        Args:
            user_id: Account that must own the deployment.
            deployment_id: Deployment to read.

        Returns:
            The deployment record, or ``None`` when it is not owned by the account.
        """
        with self._database.session() as session:
            return DeploymentRepository.get(session, deployment_id, user_id)

    def start(self, user_id: str, deployment_id: str) -> int:
        """Start a pending or paused deployment.

        Args:
            user_id: Account that owns the deployment.
            deployment_id: Deployment to start.

        Returns:
            The number of changed rows.
        """
        with self._database.session() as session:
            return DeploymentRepository.start(session, deployment_id, user_id)

    def pause(self, user_id: str, deployment_id: str, *, reason: str) -> int:
        """Pause a running deployment.

        Args:
            user_id: Account that owns the deployment.
            deployment_id: Deployment to pause.
            reason: Required reason saved with the pause.

        Returns:
            The number of changed rows.
        """
        with self._database.session() as session:
            return DeploymentRepository.pause(
                session, deployment_id, user_id, reason=reason
            )

    def stop(self, user_id: str, deployment_id: str, *, reason: str) -> int:
        """Stop a deployment.

        Args:
            user_id: Account that owns the deployment.
            deployment_id: Deployment to stop.
            reason: Required reason saved with the stop.

        Returns:
            The number of changed rows.
        """
        with self._database.session() as session:
            return DeploymentRepository.stop(
                session, deployment_id, user_id, reason=reason
            )

    def orders(
        self, user_id: str, deployment_id: str, *, limit: int
    ) -> list[dict[str, Any]]:
        """Read recent orders for one deployment.

        Args:
            user_id: Account that owns the orders.
            deployment_id: Deployment whose orders are requested.
            limit: Maximum number of orders to return.

        Returns:
            The matching orders in repository order.
        """
        with self._database.session() as session:
            rows, _ = OrderRepository.list_for_user(
                session,
                user_id,
                deployment_id=deployment_id,
                limit=limit,
            )
        return rows
