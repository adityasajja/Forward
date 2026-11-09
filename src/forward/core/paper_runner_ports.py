"""Read-only operations used by paper runner health and status screens."""

from __future__ import annotations

from typing import Any, Protocol


class PaperHealthRunnerPort(Protocol):
    """Read runner state needed to measure paper deployment health."""

    def resolve_rules(self, deployment: dict[str, Any]) -> tuple[Any, Any] | None:
        """Resolve immutable strategy rules for one deployment.

        Args:
            deployment: The stored deployment row.

        Returns:
            The entry and exit rules, or ``None`` when they cannot be loaded.
        """

    def running_deployments(self) -> list[dict[str, Any]]:
        """Return deployment rows the runner currently evaluates.

        Returns:
            Rows for deployments evaluated by the runner.
        """

    def status(self) -> dict[str, Any]:
        """Return the runner's current process and deployment state.

        Returns:
            Runner state for status and health screens.
        """


class DeploymentMonitorRunnerPort(Protocol):
    """Read runner state needed to monitor one deployment."""

    def deployment_status(self, deployment_id: str) -> dict[str, Any] | None:
        """Return the live state for one deployment.

        Args:
            deployment_id: Deployment whose runner state is requested.

        Returns:
            Current state, or ``None`` when the runner has no matching loop.
        """

    @property
    def running(self) -> bool:
        """Return whether the runner loop is active."""
