"""Ports used by the analytics application service."""

from __future__ import annotations

from typing import Any, Protocol


class AttributionReaderPort(Protocol):
    """Read saved trade attributions for one account."""

    def rows(self, user_id: str, **filters: Any) -> list[dict[str, Any]]:
        """Return matching attribution rows.

        Args:
            user_id: Account that owns the rows.
            **filters: Repository filters, including page settings.

        Returns:
            Matching attribution rows.
        """

    def all_rows(self, user_id: str) -> list[dict[str, Any]]:
        """Return every saved attribution for an account.

        Args:
            user_id: Account that owns the rows.

        Returns:
            All attribution rows.
        """

    def get(self, user_id: str, trade_id: str) -> dict[str, Any] | None:
        """Return one account-owned attribution.

        Args:
            user_id: Account that owns the attribution.
            trade_id: Trade identifier.

        Returns:
            The attribution row, or ``None`` when it is not found.
        """

    def coverage(self, user_id: str, attributed: int) -> dict[str, Any]:
        """Compare attributed rows with the account's closed trades.

        Args:
            user_id: Account to measure.
            attributed: Number of rows included in the current report.

        Returns:
            Coverage counts and completeness.
        """
