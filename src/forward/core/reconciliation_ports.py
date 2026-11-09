"""Storage operations for order reconciliation."""

from __future__ import annotations

from typing import Any, Protocol


class ReconciliationStoragePort(Protocol):
    """Read and persist platform records used by reconciliation."""

    def open_orders(self, user_id: str) -> list[dict[str, Any]]:
        """Return the user's live open orders."""

    def record(self, user_id: str, report: Any, actor: str | None) -> str:
        """Persist one reconciliation report and its audit event."""

    def latest(self, user_id: str, scope: str | None) -> dict[str, Any] | None:
        """Return the latest report for the requested scope."""

    def history(
        self, user_id: str, severity: str | None, limit: int
    ) -> tuple[list[dict[str, Any]], int]:
        """Return a page of reports and its total count."""

    def worst_unacknowledged(self, user_id: str) -> dict[str, Any] | None:
        """Return the most severe report that is not acknowledged."""
