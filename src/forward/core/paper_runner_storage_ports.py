"""Ports for paper runner storage."""

from __future__ import annotations

from typing import Any, Protocol


class PaperRunnerStoragePort(Protocol):
    """Load deployment and immutable strategy data for the paper runner."""

    def running_deployments(self) -> list[dict[str, Any]]:
        """Return every active paper and live deployment.

        Returns:

            Deployment rows eligible for evaluation.
        """

    def strategy_version(self, strategy_id: str, version: int) -> dict[str, Any] | None:
        """Return one exact strategy version.

        Args:

            strategy_id: Stable strategy identifier.

            version: Immutable version number.

        Returns:

            The version row, or ``None`` when it does not exist.
        """

    def strategy_versions(
        self, keys: list[tuple[str, int]]
    ) -> dict[tuple[str, int], dict[str, Any]]:
        """Return the exact versions named by ``keys``.

        Args:

            keys: Strategy identifiers paired with exact version numbers.

        Returns:

            Version rows keyed by the supplied strategy and version pairs.
        """
