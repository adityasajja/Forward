"""Ports for forward strategy tracking data."""

from __future__ import annotations

from typing import Any, Protocol


class ForwardTrackingPort(Protocol):
    """Read saved state for forward research summaries."""

    def oversold_volatile_state(self) -> dict[str, Any]:
        """Read the saved oversold-and-volatile tracker state.

        Returns:
            The saved signals and their grading state.
        """

    def gap_plan_state(self) -> dict[str, Any]:
        """Read the saved gap-plan state.

        Returns:
            The saved plan, trades, and current market reading.
        """


class ForwardTradeStoragePort(Protocol):
    """Read forward trades owned by one account for one strategy."""

    def forward_trades(self, user_id: str, strategy_id: str) -> list[dict[str, Any]]:
        """Return forward journal rows ordered by entry time.

        Args:
            user_id: Account that owns the trades.
            strategy_id: Strategy whose forward rows are requested.

        Returns:
            Journal rows ordered by entry time.
        """


class ForwardCheckStoragePort(ForwardTradeStoragePort, Protocol):
    """Read completed backtest and forward trades for one account strategy."""

    def latest_backtest_trades(
        self, user_id: str, strategy_id: str, version: int | None
    ) -> tuple[str | None, list[dict[str, Any]]]:
        """Return trades from the latest completed matching backtest.

        Args:
            user_id: Account that owns the strategy and run.
            strategy_id: Strategy being checked.
            version: Exact strategy version, or ``None`` for any version.

        Returns:
            The latest run ID and its closed trade rows, or ``(None, [])``.
        """
