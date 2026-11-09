"""Ports for paper deployment."""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any, Protocol


class PaperDeploymentUnitOfWorkPort(Protocol):
    """Read a saved strategy and create its deployment in one transaction."""

    def has_saved_strategy(self, user_id: str, strategy_id: str) -> bool:
        """Return whether the account owns a saved strategy.

        Args:

            user_id: Account that owns the strategy.

            strategy_id: Stable strategy identifier.

        Returns:

            Whether the strategy belongs to the account.
        """

    def strategy_version(self, strategy_id: str, version: int) -> dict[str, Any] | None:
        """Read one exact saved version.

        Args:

            strategy_id: Stable strategy identifier.

            version: Immutable strategy version.

        Returns:

            The stored version, or ``None`` when it does not exist.
        """

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


class PaperDeploymentStoragePort(Protocol):
    """Store and update paper deployment records."""

    def unit_of_work(self) -> AbstractContextManager[PaperDeploymentUnitOfWorkPort]:
        """Open the transaction used to validate and create a deployment.

        Returns:

            A unit of work backed by one storage transaction.
        """

    def deployments(self, user_id: str, status: str | None) -> list[dict[str, Any]]:
        """Read an account's deployments, optionally filtered by status.

        Args:

            user_id: Account whose deployments are requested.

            status: Optional exact status filter.

        Returns:

            Matching deployment records.
        """

    def running_deployments(self, user_id: str) -> list[dict[str, Any]]:
        """Read the account's running deployments.

        Args:

            user_id: Account whose running deployments are requested.

        Returns:

            Running deployment records.
        """

    def deployment(self, user_id: str, deployment_id: str) -> dict[str, Any] | None:
        """Read one deployment owned by the account.

        Args:

            user_id: Account that must own the deployment.

            deployment_id: Deployment to read.

        Returns:

            The deployment, or ``None`` when it is not owned by the account.
        """

    def start(self, user_id: str, deployment_id: str) -> int:
        """Start a pending or paused deployment.

        Args:

            user_id: Account that owns the deployment.

            deployment_id: Deployment to start.

        Returns:

            Number of rows changed.
        """

    def pause(self, user_id: str, deployment_id: str, *, reason: str) -> int:
        """Pause a running deployment.

        Args:

            user_id: Account that owns the deployment.

            deployment_id: Deployment to pause.

            reason: Required reason saved with the state change.

        Returns:

            Number of rows changed.
        """

    def stop(self, user_id: str, deployment_id: str, *, reason: str) -> int:
        """Stop a deployment.

        Args:

            user_id: Account that owns the deployment.

            deployment_id: Deployment to stop.

            reason: Required reason saved with the state change.

        Returns:

            Number of rows changed.
        """

    def orders(self, user_id: str, deployment_id: str, *, limit: int) -> list[dict[str, Any]]:
        """Read recent orders for one account-owned deployment.

        Args:

            user_id: Account that owns the deployment.

            deployment_id: Deployment whose orders are requested.

            limit: Maximum number of orders to return.

        Returns:

            Matching order records.
        """
