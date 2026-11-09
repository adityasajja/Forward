"""Storage and market-data operations for signal context."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol


class SignalContextStoragePort(Protocol):
    """Store signal contexts and read the evidence used to score them."""

    def save_context(self, row: dict[str, Any]) -> int:
        """Save one signal context.

        Args:

            row: Persistable values for one context.

        Returns:

            One when inserted, or zero when the signal already exists.

        """

    def replace_run_contexts(self, run_id: str, rows: list[dict[str, Any]]) -> int:
        """Replace every context row for one completed backtest run.

        Args:

            run_id: Backtest run whose rows are replaced.

            rows: Complete context rows for that run.

        Returns:

            Number of rows stored.

        """

    def list_contexts(
        self,
        user_id: str,
        *,
        limit: int,
        offset: int,
        source: str | None,
        strategy_id: str | None,
        context_class: str | None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return one user's filtered context page and total row count.

        Args:

            user_id: Account that owns the contexts.

            limit: Maximum rows in the page.

            offset: Number of rows to skip.

            source: Optional signal source filter.

            strategy_id: Optional strategy filter.

            context_class: Optional context class filter.

        Returns:

            Page rows and the total matching count.

        """

    def context(self, user_id: str, signal_id: str) -> dict[str, Any] | None:
        """Return one context owned by a user.

        Args:

            user_id: Account that owns the context.

            signal_id: Stable identifier for the signal.

        Returns:

            The context row, or ``None`` when no matching row exists.

        """

    def analytics_contexts(
        self, user_id: str, *, strategy_id: str | None, source: str | None
    ) -> list[dict[str, Any]]:
        """Return contexts for outcome analysis.

        Args:

            user_id: Account that owns the contexts.

            strategy_id: Optional strategy filter.

            source: Optional signal source filter.

        Returns:

            Context rows in signal-time order.

        """

    def outcome_data(
        self,
        user_id: str,
        trade_ids: Sequence[int],
        signal_ids: Sequence[str],
        strategy_version: int | None,
    ) -> Mapping[str, Any]:
        """Read records used to resolve context outcomes.

        Args:

            user_id: Account that owns the contexts and live outcomes.

            trade_ids: Backtest trades referenced by the contexts.

            signal_ids: Signal identifiers used to find matching orders.

            strategy_version: Optional version filter for live orders.

        Returns:

            Backtest trades, matching orders, and closed journal episodes.

        """

    def backtest_data(
        self, run_id: str, user_id: str
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        """Return one account-owned backtest run and its trades.

        Args:

            run_id: Backtest run to read.

            user_id: Account that must own the run.

        Returns:

            The run and trade rows, or ``None`` for a missing run.

        """

    def record_observations(self, observations: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        """Store supported signal-context learning observations.

        Args:

            observations: Learning rows that meet evidence requirements.

        Returns:

            Stored observation rows.

        """


class SignalContextMarketDataPort(Protocol):
    """Read market frames and sector names used for signal-context scoring."""

    def daily_frames(self) -> Mapping[str, Any]:
        """Return cached daily frames keyed by their file symbols.

        Returns:

            All daily frames available to the context market scan.

        """

    def daily_frame(self, symbol: str) -> Any | None:
        """Return one symbol's cached daily frame.

        Args:

            symbol: Canonical trading symbol.

        Returns:

            The cached frame, or ``None`` when it is not available.

        """

    def sector_table(self) -> Mapping[str, str]:
        """Return the canonical symbol-to-sector mapping.

        Returns:

            Sector names keyed by canonical trading symbol.

        """


class SignalContextMarketIntelPort(Protocol):
    """Read live market-intelligence snapshots for context enrichment."""

    def get_benchmark_frame_and_provenance(self) -> tuple[Any, Any]:
        """Return the benchmark frame and its source record.

        Returns:

            Benchmark market bars and provenance.

        """

    def get_summary(self) -> Any:
        """Return the current market-wide summary.

        Returns:

            The latest market summary.

        """

    def get_sectors(self) -> list[Any]:
        """Return current sector measurements.

        Returns:

            Sector summaries for the latest market scan.

        """

    def get_stock_context(self, symbol: str) -> Any | None:
        """Return the latest market context for one symbol.

        Args:

            symbol: Canonical trading symbol.

        Returns:

            Stock context, or ``None`` when the market scan has no record.

        """


class SignalContextEnrichmentPort(Protocol):
    """Enrich completed backtests and live signals with market context."""

    def enrich_live(self, **values: Any) -> Any | None:
        """Build and store context for one live or paper signal.

        Args:

            **values: Signal identity, market frames, and point-in-time hints.

        Returns:

            Enrichment result when a context was recorded, else ``None``.

        """

    def enrich_run(self, run_id: str, user_id: str) -> dict[str, Any]:
        """Build and store context for the trades in one backtest run.

        Args:

            run_id: Completed backtest run to enrich.

            user_id: Account that owns the run.

        Returns:

            Summary of recorded and skipped trade contexts.

        """
