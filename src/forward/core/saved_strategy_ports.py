"""Ports for saved strategy."""

from __future__ import annotations

from typing import Any, Protocol


class SavedStrategyPort(Protocol):
    """Read an owned strategy and one of its immutable versions."""

    def get(self, user_id: str, strategy_id: str) -> dict[str, Any]:
        """Return a strategy owned by the account.

        Args:
            user_id: Account that owns the strategy.
            strategy_id: Strategy identifier.

        Returns:
            The saved strategy record.
        """

    def version(self, user_id: str, strategy_id: str, version: int) -> dict[str, Any]:
        """Return one exact version owned by the account.

        Args:
            user_id: Account that owns the strategy.
            strategy_id: Strategy identifier.
            version: Exact immutable version number.

        Returns:
            The saved version record.
        """
