"""SQL adapter for backtest run and result storage."""

from __future__ import annotations

from typing import Any

from forward.appdb.backtest_repository import (
    BacktestArtefactRepository,
    BacktestRunRepository,
)
from forward.appdb.engine import AppDatabase
from forward.appdb.strategy_repository import StrategyRepository
from forward.core.backtest_ports import BacktestStoragePort


class SqlBacktestStorage(BacktestStoragePort):
    """Store backtest inputs and outputs with the application database."""

    def __init__(self, db: AppDatabase) -> None:
        """Create the storage adapter.

        Args:
            db: The configured application database.
        """
        self._db = db

    def saved_strategies(self, owner_user_id: str) -> list[dict[str, Any]]:
        """Return saved strategies for an account or the CLI operator.

        Args:
            owner_user_id: Account whose saved strategies are requested.

        Returns:
            The account's saved strategies.
        """
        with self._db.session() as session:
            return StrategyRepository.list_for_user(session, owner_user_id)

    def strategy_versions(
        self, strategy_id: str, owner_user_id: str
    ) -> list[dict[str, Any]]:
        """Return saved versions for an account-owned strategy.

        Args:
            strategy_id: Strategy whose versions are requested.
            owner_user_id: Account that owns the strategy.

        Returns:
            The strategy versions, newest first.
        """
        with self._db.session() as session:
            return StrategyRepository.versions_for_user(session, strategy_id, owner_user_id)

    def strategy_version(
        self, strategy_id: str, version: int, owner_user_id: str
    ) -> dict[str, Any] | None:
        """Return one exact version of an account-owned strategy.

        Args:
            strategy_id: Strategy that owns the version.
            version: Exact version number.
            owner_user_id: Account that owns the strategy.

        Returns:
            The stored version, or ``None`` when it does not exist.
        """
        with self._db.session() as session:
            return StrategyRepository.version_for_user(
                session, strategy_id, version, owner_user_id
            )

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
            config: Validated run configuration.
            engine_key: Built-in strategy key, when applicable.
            strategy_id: Saved strategy ID, when applicable.
            strategy_version: Exact saved strategy version, when applicable.

        Returns:
            The created run row.
        """
        with self._db.session() as session:
            return BacktestRunRepository.create(
                session,
                user_id=user_id,
                config=config,
                engine_key=engine_key,
                strategy_id=strategy_id,
                strategy_version=strategy_version,
            )

    def start_run(self, run_id: str, user_id: str, progress: float) -> None:
        """Mark a run as running and store its initial progress.

        Args:
            run_id: Run to start.
            user_id: Account that owns the run.
            progress: Initial progress value.
        """
        with self._db.session() as session:
            BacktestRunRepository.mark_running(session, run_id, user_id)
            BacktestRunRepository.set_progress(session, run_id, user_id, progress)

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
        """Write all result artefacts and complete the run atomically.

        Args:
            run_id: Run that produced the results.
            user_id: Account that owns the run.
            metrics: Calculated summary metrics.
            data_fingerprint: Fingerprint of the input data.
            equity: Ordered equity points.
            drawdown: Ordered drawdown points.
            exposure: Ordered exposure points.
            trades: Completed trade rows.
            monthly: Monthly return rows.

        Returns:
            ``True`` when the run transitioned to completed.
        """
        with self._db.session() as session:
            completed = BacktestRunRepository.complete(
                session,
                run_id,
                user_id,
                metrics=metrics,
                data_fingerprint=data_fingerprint,
            )
            if not completed:
                return False
            BacktestArtefactRepository.save_curve(session, run_id, "equity", equity)
            BacktestArtefactRepository.save_curve(session, run_id, "drawdown", drawdown)
            BacktestArtefactRepository.save_curve(session, run_id, "exposure", exposure)
            BacktestArtefactRepository.save_trades(session, run_id, trades)
            BacktestArtefactRepository.save_monthly(session, run_id, monthly)
            return True

    def fail_run(self, run_id: str, user_id: str, error: str) -> None:
        """Remove partial results and mark a run failed atomically.

        Args:
            run_id: Run that failed.
            user_id: Account that owns the run.
            error: Failure detail to store.
        """
        with self._db.session() as session:
            failed = BacktestRunRepository.fail(session, run_id, user_id, error=error)
            if not failed:
                return
            BacktestArtefactRepository.delete_for_run(session, run_id)

    def run(self, run_id: str, user_id: str) -> tuple[dict[str, Any] | None, int]:
        """Return one owned run and its completed trade count.

        Args:
            run_id: Run to read.
            user_id: Account that owns the run.

        Returns:
            The run row and its trade count, or ``(None, 0)`` if not found.
        """
        with self._db.session() as session:
            row = BacktestRunRepository.get(session, run_id, user_id)
            count = (
                BacktestArtefactRepository.trade_count(session, run_id)
                if row is not None and row["status"] == "COMPLETED"
                else 0
            )
        return row, count

    def history(
        self,
        user_id: str,
        *,
        status: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return a page of runs and the total matching row count.

        Args:
            user_id: Account whose runs should be listed.
            status: Optional run status filter.
            limit: Maximum page size.
            offset: Number of matching rows to skip.

        Returns:
            The run rows and total count.
        """
        with self._db.session() as session:
            return BacktestRunRepository.list_for_user(
                session, user_id, status=status, limit=limit, offset=offset
            )

    def curves(self, run_id: str) -> dict[str, list[dict[str, Any]]]:
        """Return all stored result curves for a run.

        Args:
            run_id: Run whose curves should be read.

        Returns:
            Curves grouped by their result name.
        """
        with self._db.session() as session:
            return {
                name: BacktestArtefactRepository.curve(session, run_id, name)
                for name in ("equity", "drawdown", "exposure")
            }

    def trades(self, run_id: str, *, limit: int, offset: int) -> tuple[int, list[dict[str, Any]]]:
        """Return a page of trades and its total row count.

        Args:
            run_id: Run whose trades should be read.
            limit: Maximum page size.
            offset: Number of rows to skip.

        Returns:
            The total trade count and the requested rows.
        """
        with self._db.session() as session:
            total = BacktestArtefactRepository.trade_count(session, run_id)
            rows = BacktestArtefactRepository.trades(session, run_id, limit=limit, offset=offset)
        return total, rows

    def trade(self, run_id: str, seq: int) -> dict[str, Any] | None:
        """Return one trade by its sequence number.

        Args:
            run_id: Run that contains the trade.
            seq: Trade sequence number.

        Returns:
            The trade row, or ``None`` when it does not exist.
        """
        with self._db.session() as session:
            return BacktestArtefactRepository.trade(session, run_id, seq)

    def monthly(self, run_id: str) -> list[dict[str, Any]]:
        """Return monthly result rows for a run.

        Args:
            run_id: Run whose monthly results should be read.

        Returns:
            Monthly return rows ordered by year and month.
        """
        with self._db.session() as session:
            return BacktestArtefactRepository.monthly(session, run_id)

    def cancel_run(self, run_id: str, user_id: str) -> bool:
        """Cancel a run that has not reached a terminal state.

        Args:
            run_id: Run to cancel.
            user_id: Account that owns the run.

        Returns:
            ``True`` when the run changed to cancelled.
        """
        with self._db.session() as session:
            return bool(BacktestRunRepository.cancel(session, run_id, user_id))

    def get_run(self, run_id: str, user_id: str) -> dict[str, Any] | None:
        """Return one owned run without its result artefacts.

        Args:
            run_id: Run to read.
            user_id: Account that owns the run.

        Returns:
            The run row, or ``None`` when it does not exist.
        """
        with self._db.session() as session:
            return BacktestRunRepository.get(session, run_id, user_id)
