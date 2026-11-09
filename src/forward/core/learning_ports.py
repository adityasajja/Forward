"""Ports for the related application workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol


class DailyLearningStoragePort(Protocol):
    """Store learning observations and completed cycle reports."""

    def record_observations(self, observations: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        """Record statistically valid observations in one transaction."""

    def save_cycle(self, report: dict[str, Any], user_id: str) -> None:
        """Store one account's cycle report and update its history.

        Args:
            report: JSON-compatible cycle report.
            user_id: Account that owns the report.
        """

    def latest_cycle(self, user_id: str) -> dict[str, Any] | None:
        """Return the latest cycle report for one account.

        Args:
            user_id: Account that owns the report.
        """

    def cycle_history(self, user_id: str, limit: int) -> list[dict[str, Any]]:
        """Return recent cycle reports for one account.

        Args:
            user_id: Account that owns the reports.
            limit: Maximum number of reports to return.
        """


class LearningStoragePort(Protocol):
    """Read stored trade evidence for learning reports."""

    def completed_backtest_runs(self, user_id: str | None, limit: int) -> list[dict[str, Any]]:
        """Read completed runs, newest first.

        Args:

            user_id: Account that owns the runs, or ``None`` for all accounts.

            limit: Maximum number of runs to return.

        Returns:

            Completed run rows.

        """

    def backtest_trades(self, run_ids: Sequence[str]) -> list[dict[str, Any]]:
        """Read trades for selected backtest runs.

        Args:

            run_ids: Run identifiers to read.

        Returns:

            Trade rows ordered by run and sequence.

        """

    def journal_rows(self, user_id: str | None, limit: int) -> list[dict[str, Any]]:
        """Read journal episodes in entry-time order.

        Args:

            user_id: Account that owns the episodes, or ``None`` for all accounts.

            limit: Maximum number of episodes to return.

        Returns:

            Journal rows.

        """

    def deployment_modes(self, user_id: str | None) -> dict[str, str]:
        """Read deployment modes keyed by deployment id.

        Args:

            user_id: Account that owns the deployments, or None for platform analysis.

        Returns:

            Deployment id to upper-case mode mapping.

        """

    def trade_attributions(
        self, user_id: str, trade_ids: Sequence[str]
    ) -> dict[str, dict[str, Any]]:
        """Read saved attributions for selected trades.

        Args:

            user_id: Account that owns the trades.

            trade_ids: Trade identifiers to read.

        Returns:

            Attribution rows keyed by trade id.

        """

    def recorded_signal_contexts(
        self, user_id: str | None
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Read orders and contexts used to link journal episodes.

        Args:

            user_id: Account that owns the records, or ``None`` for all accounts.

        Returns:

            Ordered orders and their recorded signal contexts.

        """

    def evidence_counts(self, forward_grade: str, user_id: str | None) -> dict[str, int]:
        """Count stored backtest and journal evidence.

        Args:

            forward_grade: Stored grade that represents forward evidence.

        Returns:

            Counts used by the learning status view.

        """

    def signal_context_count(self, user_id: str, strategy_id: str) -> int:
        """Count recorded signal contexts for one strategy.

        Args:

            user_id: Account that owns the contexts.

            strategy_id: Strategy whose contexts are counted.

        Returns:

            Number of recorded contexts.

        """

    def list_observations(
        self,
        *,
        user_id: str | None,
        strategy_id: str | None,
        strategy_version: int | None,
        evidence_class: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Read learning observations using the requested filters.

        Args:
            user_id: Account that owns the observations, or None for platform analysis.
            strategy_id: Optional strategy filter.

            strategy_version: Optional version filter.

            evidence_class: Optional evidence-class filter.

            limit: Maximum number of rows to return.

        Returns:

            Matching observation rows.

        """

    def observation_counts_by_strategy(
        self, evidence_classes: Sequence[str], user_id: str
    ) -> dict[str, int]:
        """Count all matching stored observations by strategy.

        Args:
            evidence_classes: Evidence classes whose observations are counted.
            user_id: Account that owns the observations, or None for platform analysis.

        Returns:
            Complete counts keyed by strategy ID.
        """


class LearningArtifactsPort(Protocol):
    """Read learning inputs and store learning report files."""

    def paper_ledger(self) -> Any:
        """Return the paper learning ledger.

        Returns:

            Ledger records and their evidence counts.

        """

    def sector_table(self) -> Mapping[str, str]:
        """Return the canonical symbol-to-industry map.

        Returns:

            Industry names keyed by canonical symbol.

        """

    def write_snapshot(self, payload: Mapping[str, Any]) -> Mapping[str, str]:
        """Store the latest snapshot and its timestamped report.

        Args:

            payload: JSON-compatible learning snapshot.

        Returns:

            Paths of the written report and latest snapshot.

        """


class SetupStatsCachePort(Protocol):
    """Load and save the persistent cache for signal setup statistics."""

    def load(self) -> tuple[str, dict[str, Any]] | None:
        """Return the stored input key and statistics, if present."""

    def save(self, input_key: str, statistics: dict[str, Any]) -> None:
        """Persist statistics for the supplied input key."""


class SetupStatisticsPort(Protocol):
    """Read backtested statistics for intelligent signal setups."""

    def get(self) -> dict[str, Any]:
        """Return all setup statistics without waiting for a refresh."""

    def for_setup(self, setup: str) -> Any | None:
        """Return statistics for one setup, if available."""

    def warm(self, *, background: bool = True) -> None:
        """Refresh statistics in the background or before returning."""
