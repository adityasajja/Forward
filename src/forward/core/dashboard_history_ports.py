"""Port for dashboard market history."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from forward.core.market_models import DailyClose, DashboardHistorySample


class DashboardHistoryPort(Protocol):
    """Read daily closes used by dashboard metrics."""

    def latest_closes(self, symbols: Sequence[str], exchange: str) -> Mapping[str, DailyClose]:
        """Return the newest cached close for each requested symbol.

        Args:
            symbols: Canonical symbols to read.
            exchange: Exchange segment that stores the history.

        Returns:
            Latest close and session date for each available symbol.
        """

    def breadth_history(
        self,
        exchange: str,
        sample_size: int,
        lookback_bars: int,
        minimum_bars: int,
    ) -> DashboardHistorySample:
        """Return recent closes for dashboard breadth metrics.

        Args:
            exchange: Exchange segment that stores the history.
            sample_size: Maximum number of symbols to return.
            lookback_bars: Number of recent closes needed per symbol.
            minimum_bars: Minimum history needed for a symbol.

        Returns:
            Sampled closes and the full exchange universe size.
        """
