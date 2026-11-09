"""Ports for strategy universe data."""

from __future__ import annotations

from typing import Protocol


class StrategyUniversePort(Protocol):
    """Read the symbols used by strategy research."""

    def researched_universe(self) -> list[str]:
        """Return symbols available to the strategy workflow.

        Returns:
            Symbols with enough cached history for the current workflow.
        """
