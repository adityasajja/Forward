"""Interfaces used by historical research and backtests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from forward.core.instrument_models import Instrument
from forward.core.market_models import MarketSnapshot


class BacktestFeedPort(Protocol):
    """Provide ordered market snapshots and their instrument definitions."""

    def load(self) -> Sequence[MarketSnapshot]:
        """Return snapshots in ascending timestamp order.

        Returns:
            Market snapshots for the backtest period.
        """

    @property
    def instruments(self) -> Mapping[str, Instrument]:
        """Return instruments keyed by canonical trading symbol.

        Returns:
            Instruments available in the feed.
        """


class BacktestMarketDataPort(Protocol):
    """Load backtest frames and convert them into an engine feed."""

    def load_frames(
        self, symbols: Sequence[str], exchange: str, timeframe: str, end: str | None
    ) -> tuple[Mapping[str, Any], list[str]]:
        """Load requested market frames and explain any data selection.

        Args:
            symbols: Trading symbols requested by the run.
            exchange: Exchange segment for the requested data.
            timeframe: Candle size selected for the run.
            end: Optional inclusive final market date.

        Returns:
            Frames keyed by canonical symbol and data selection warnings.
        """

    def build_feed(
        self, frames: Mapping[str, Any], exchange: str, timeframe: str
    ) -> BacktestFeedPort:
        """Convert loaded frames into the ordered feed consumed by the engine.

        Args:
            frames: Clipped and warmup-aware frames keyed by symbol.
            exchange: Exchange segment for the instruments.
            timeframe: Candle size represented by the frames.

        Returns:
            A feed with snapshots and instrument definitions.
        """


class ResearchHistoryPort(Protocol):
    """Load deep daily history for a requested research universe."""

    def load_daily_feed(
        self, symbols: Sequence[str], exchange: str, lookback_days: int
    ) -> tuple[BacktestFeedPort, list[str]]:
        """Build a backtest feed from the symbols with usable broker history.

        Args:
            symbols: Exact trading symbols to request.
            exchange: Exchange segment that lists the instruments.
            lookback_days: Calendar days of daily bars to request.

        Returns:
            The feed and sorted symbols that contributed bars.
        """


class CachedResearchHistoryPort(Protocol):
    """Load available daily bars from a local research cache."""

    def load_cached_daily_feed(
        self, symbols: Sequence[str], exchange: str
    ) -> tuple[BacktestFeedPort, list[str]]:
        """Build a feed from requested symbols that have cached bars.

        Args:
            symbols: Exact trading symbols to load.
            exchange: Exchange segment that stores the bars.

        Returns:
            The feed and sorted symbols included in it.

        Raises:
            FileNotFoundError: If the exchange cache does not exist.
            LookupError: If none of the requested symbols has cached bars.
        """


class HistoricalFeedPort(Protocol):
    """Load real historical bars for a stated market universe."""

    def load(
        self, *, exchange: str | None, symbols: Sequence[str] | None, universe: str | None
    ) -> BacktestFeedPort:
        """Load a feed for the requested exchange symbols or named universe.

        Args:
            exchange: Required exchange, or ``None`` when records identify it.
            symbols: Explicit symbols, or ``None`` when a universe is supplied.
            universe: Explicit stored universe, or ``None`` when symbols are supplied.

        Returns:
            A feed containing the requested real historical bars.
        """


class BacktestStoragePort(Protocol):
    """Store backtest runs, their strategy versions, and result artefacts."""

    def saved_strategies(self, owner_user_id: str) -> list[dict[str, Any]]:
        """Return saved strategies for an account or the CLI operator.

        Args:
            owner_user_id: Account whose saved strategies are requested.

        Returns:
            The saved strategies visible to that caller.
        """

    def strategy_versions(
        self, strategy_id: str, owner_user_id: str
    ) -> list[dict[str, Any]]:
        """Return the saved versions for one account-owned strategy.

        Args:
            strategy_id: Strategy whose versions are requested.
            owner_user_id: Account that owns the strategy.

        Returns:
            The saved versions, newest first.
        """

    def strategy_version(
        self, strategy_id: str, version: int, owner_user_id: str
    ) -> dict[str, Any] | None:
        """Return one exact version of an account-owned strategy.

        Args:
            strategy_id: Strategy that owns the version.
            version: Exact version number.
            owner_user_id: Account that owns the strategy.

        Returns:
            The version row, or ``None`` when it does not exist.
        """

    def create_run(
        self,
        *,
        user_id: str,
        config: dict[str, Any],
        engine_key: str | None,
        strategy_id: str | None,
        strategy_version: int | None,
    ) -> dict[str, Any]:
        """Create a queued backtest run.

        Args:
            user_id: Account that owns the run.
            config: Validated backtest configuration.
            engine_key: Built-in strategy key, when applicable.
            strategy_id: Saved strategy ID, when applicable.
            strategy_version: Exact saved version, when applicable.

        Returns:
            The created run row.
        """

    def start_run(self, run_id: str, user_id: str, progress: float) -> None:
        """Mark a run as running and store its first progress value.

        Args:
            run_id: Run to start.
            user_id: Account that owns the run.
            progress: Initial progress value.
        """

    def complete_run(
        self,
        run_id: str,
        user_id: str,
        *,
        metrics: dict[str, Any],
        data_fingerprint: str | None,
        equity: list[tuple[Any, float]],
        drawdown: list[tuple[Any, float]],
        exposure: list[tuple[Any, float]],
        trades: list[dict[str, Any]],
        monthly: list[dict[str, Any]],
    ) -> bool:
        """Store result artefacts and complete the run in one transaction.

        Args:
            run_id: Run that produced the results.
            user_id: Account that owns the run.
            metrics: Calculated summary metrics.
            data_fingerprint: Fingerprint of the input data, when available.
            equity: Ordered equity points.
            drawdown: Ordered drawdown points.
            exposure: Ordered exposure points.
            trades: Completed trade rows.
            monthly: Monthly return rows.

        Returns:
            Whether the run changed to completed.
        """

    def fail_run(self, run_id: str, user_id: str, error: str) -> None:
        """Remove partial results and mark the run failed together.

        Args:
            run_id: Run that failed.
            user_id: Account that owns the run.
            error: Failure detail to store.
        """

    def run(self, run_id: str, user_id: str) -> tuple[dict[str, Any] | None, int]:
        """Return one owned run and its completed trade count.

        Args:
            run_id: Run to read.
            user_id: Account that owns the run.

        Returns:
            The run row and its trade count, or ``(None, 0)`` when absent.
        """

    def history(
        self, user_id: str, *, status: str | None, limit: int, offset: int
    ) -> tuple[list[dict[str, Any]], int]:
        """Return a page of runs and the total matching row count.

        Args:
            user_id: Account whose runs are requested.
            status: Optional run status filter.
            limit: Maximum number of rows to return.
            offset: Number of matching rows to skip.

        Returns:
            The requested rows and total matching row count.
        """

    def curves(self, run_id: str) -> dict[str, list[dict[str, Any]]]:
        """Return the equity, drawdown, and exposure series for one run.

        Args:
            run_id: Run whose result curves are requested.

        Returns:
            Curves grouped by their result name.
        """

    def trades(self, run_id: str, *, limit: int, offset: int) -> tuple[int, list[dict[str, Any]]]:
        """Return a page of trades and its total row count.

        Args:
            run_id: Run whose trades are requested.
            limit: Maximum number of rows to return.
            offset: Number of rows to skip.

        Returns:
            The total trade count and requested rows.
        """

    def trade(self, run_id: str, seq: int) -> dict[str, Any] | None:
        """Return one trade by its sequence number.

        Args:
            run_id: Run that owns the trade.
            seq: Trade sequence number.

        Returns:
            The trade row, or ``None`` when it does not exist.
        """

    def monthly(self, run_id: str) -> list[dict[str, Any]]:
        """Return monthly result rows for one run.

        Args:
            run_id: Run whose monthly results are requested.

        Returns:
            Monthly result rows ordered by year and month.
        """

    def cancel_run(self, run_id: str, user_id: str) -> bool:
        """Cancel a run that has not reached a terminal state.

        Args:
            run_id: Run to cancel.
            user_id: Account that owns the run.

        Returns:
            Whether the run changed to cancelled.
        """

    def get_run(self, run_id: str, user_id: str) -> dict[str, Any] | None:
        """Return one owned run without its large result artefacts.

        Args:
            run_id: Run to read.
            user_id: Account that owns the run.

        Returns:
            The run row, or ``None`` when it does not exist.
        """
