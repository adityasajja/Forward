"""Ports for paper health."""

from __future__ import annotations

from typing import Any, Protocol


class PaperHealthStoragePort(Protocol):
    """Read deployment and strategy data for paper-health checks."""

    def health_deployments(self, user_id: str) -> list[dict[str, Any]]:
        """Return active and paused deployments with their exact definitions.

        Args:

            user_id: Account whose deployments should be checked.

        Returns:

            Owned deployments in running or paused state with their version

            definitions attached.
        """
