"""Provide deep historical feeds for research runs."""

from __future__ import annotations

from collections.abc import Sequence

from forward.core.backtest_ports import (
    BacktestFeedPort,
    CachedResearchHistoryPort,
    ResearchHistoryPort,
)


class ResearchHistoryService:
    """Load research data through a configured history port."""

    def __init__(
        self,
        cached_history: CachedResearchHistoryPort,
        deep_history: ResearchHistoryPort | None = None,
        *,
        default_symbols: Sequence[str],
    ) -> None:
        """Create the research-history service.

        Args:
            cached_history: Port that reads available local daily bars.
            deep_history: Port that reads deep broker history when requested.
            default_symbols: Symbols used when a research request has no symbols.
        """
        self._cached_history = cached_history
        self._deep_history = deep_history
        self._default_symbols = tuple(default_symbols)

    def load_cached_daily_feed(
        self, symbols: Sequence[str], exchange: str
    ) -> tuple[BacktestFeedPort, list[str]]:
        """Build a research feed from matching local cache files.

        Args:
            symbols: Exact trading symbols to load.
            exchange: Exchange segment that stores the bars.

        Returns:
            The feed and sorted symbols included in it.
        """
        selected_symbols = symbols or self._default_symbols
        return self._cached_history.load_cached_daily_feed(selected_symbols, exchange)

    def load_daily_feed(
        self, symbols: Sequence[str], exchange: str, lookback_days: int
    ) -> tuple[BacktestFeedPort, list[str]]:
        """Load a feed and report which requested symbols supplied history.

        Args:
            symbols: Exact trading symbols to request.
            exchange: Exchange segment that lists the instruments.
            lookback_days: Calendar days of daily bars to request.

        Returns:
            The feed and sorted symbols that contributed bars.
        """
        if self._deep_history is None:
            raise RuntimeError("deep research history adapter is not configured")
        selected_symbols = symbols or self._default_symbols
        return self._deep_history.load_daily_feed(selected_symbols, exchange, lookback_days)
