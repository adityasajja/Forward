"""Ports for the related application workflows."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Any, Protocol


class PortfolioStoragePort(Protocol):
    """Read and write the records used by portfolio decisions and reports."""

    def policy(self, user_id: str) -> str | None:
        """Return the user's stored policy JSON, or ``None`` when unset.



        Args:

            user_id: Account whose policy is requested.



        Returns:

            Stored policy JSON, or ``None`` when the user has no policy.

        """

    def save_policy(self, user_id: str, policy: str, *, actor: str, reason: str) -> datetime:
        """Save a validated policy and return its storage timestamp.



        Args:

            user_id: Account that owns the policy.

            policy: Serialized policy that has already passed validation.

            actor: User or service that made the change.

            reason: Required explanation for changing portfolio risk.



        Returns:

            The time the policy was saved.

        """

    def deployments(self, user_id: str) -> list[dict[str, Any]]:
        """Return deployments owned by a user.



        Args:

            user_id: Account that owns the deployments.



        Returns:

            The account's deployments.

        """

    def deployment(self, user_id: str, deployment_id: str) -> dict[str, Any] | None:
        """Read one deployment owned by an account.



        Args:

            user_id: Account that must own the deployment.

            deployment_id: Deployment to read.



        Returns:

            The deployment row, or ``None`` when it is not owned by the account.

        """

    def fills(self, user_id: str) -> list[dict[str, Any]]:
        """Return incremental fills for a user's orders, oldest first.



        Args:

            user_id: Account that owns the orders.



        Returns:

            Fill rows ordered by fill time.

        """

    def iter_journal_episodes(
        self, user_id: str, *, closed_only: bool
    ) -> Iterator[dict[str, Any]]:
        """Stream every journal episode owned by a user in stable order.



        Args:

            user_id: Account that owns the episodes.

            closed_only: Whether to include only closed episodes.




        Returns:

            Matching journal rows, ordered by entry time and trade ID.

        """

    def journal_episode_count(self, user_id: str, deployment_id: str) -> int:
        """Count closed journal episodes for one account deployment.



        Args:

            user_id: Account that owns the deployment.

            deployment_id: Deployment whose closed episodes are counted.



        Returns:

            Number of matching closed episodes.

        """

    def monitoring_journal_pages(
        self, user_id: str, deployment_id: str, limit: int
    ) -> tuple[list[dict[str, Any]], int, list[dict[str, Any]], int]:
        """Read closed and open journal pages with their total counts.



        Args:

            user_id: Account that owns the deployment.

            deployment_id: Deployment whose journal is requested.

            limit: Maximum rows in each page.



        Returns:

            Closed rows and count, followed by all rows and count.

        """

    def strategies(self, user_id: str) -> list[dict[str, Any]]:
        """Return strategies owned by a user for portfolio labels.



        Args:

            user_id: Account that owns the strategies.



        Returns:

            Matching strategy rows.

        """


class ChampionStoragePort(Protocol):
    """Read owned strategy versions and running paper deployments for comparison."""

    def strategy_versions(self, user_id: str, strategy_id: str) -> list[dict[str, Any]]:
        """Return versions only when the account owns the strategy.



        Args:

            user_id: Account that owns the strategy.

            strategy_id: Strategy whose immutable versions are requested.



        Returns:

            The strategy versions in descending version order.

        """

    def running_paper_deployments(self, user_id: str, strategy_id: str) -> list[dict[str, Any]]:
        """Return an account's running PAPER deployments for one strategy.



        Args:

            user_id: Account that owns the deployments.

            strategy_id: Strategy whose running deployments are requested.



        Returns:

            Matching deployments in the storage adapter's stable order.

        """
