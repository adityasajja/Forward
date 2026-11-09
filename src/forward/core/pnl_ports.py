"""Ports for the related application workflows."""

from __future__ import annotations

from typing import Any, Protocol


class PnlCalendarDataPort(Protocol):
    """Read and write local data used by the P&L calendar."""

    def paper_momentum_settlements(self) -> list[dict[str, Any]]:
        """Return stored weekly paper-momentum settlements.

        Returns:
            Settlement records.
        """

    def gap_plan_trades(self) -> list[dict[str, Any]]:
        """Return stored gap-plan trades.

        Returns:
            Gap-plan trade records.
        """

    def official_gl_summary(self) -> dict[str, Any]:
        """Return the broker's official realized gain and loss report.

        Returns:
            The stored official report.
        """

    def manual_realised_entries(self, user_id: str) -> dict[str, dict[str, Any]]:
        """Return manually entered daily trading profit.

        Args:
            user_id: Account that owns the entries.

        Returns:
            Entries keyed by market date.
        """

    def save_manual_realised_entries(
        self, user_id: str, entries: dict[str, dict[str, Any]]
    ) -> None:
        """Save manually entered daily trading profit.

        Args:
            user_id: Account that owns the entries.
            entries: Entries keyed by market date.
        """

    def stored_pnl_days(self) -> dict[str, dict[str, Any]]:
        """Return saved daily holdings P&L records.

        Returns:
            Holdings P&L rows keyed by market date.
        """

    def save_pnl_days(self, days: dict[str, dict[str, Any]], updated_at: str) -> None:
        """Save daily holdings P&L records.

        Args:
            days: Holdings P&L rows keyed by market date.
            updated_at: Time of this refresh.
        """

    def daily_closes(self, symbol: str) -> Any | None:
        """Return the cached daily closes for one holding.

        Args:
            symbol: Trading symbol to read.

        Returns:
            Close prices indexed by trading date, or ``None`` when unavailable.
        """
