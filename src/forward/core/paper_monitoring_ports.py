"""Ports for paper monitoring."""

from __future__ import annotations

from typing import Any, Protocol


class DeploymentMonitoringStoragePort(Protocol):
    """Read the deployment and journal data shown on its monitor screen."""

    def deployment(self, user_id: str, deployment_id: str) -> dict[str, Any] | None:
        """Read one account-owned deployment.

        Args:

            user_id: Account that owns the deployment.

            deployment_id: Deployment identifier.

        Returns:

            The deployment row, or ``None`` when it is missing.
        """

    def monitoring_journal_pages(
        self, user_id: str, deployment_id: str, limit: int
    ) -> tuple[list[dict[str, Any]], int, list[dict[str, Any]], int]:
        """Read closed and all journal pages with their total counts.

        Args:

            user_id: Account that owns the deployment.

            deployment_id: Deployment whose journal is requested.

            limit: Maximum number of rows in each page.

        Returns:

            Closed rows and count, followed by all rows and count.
        """
