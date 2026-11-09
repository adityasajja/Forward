"""SQL adapter for daily-learning observations and cycle reports."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from forward.appdb.engine import AppDatabase
from forward.appdb.learning_observation_repository import LearningObservationRepository
from forward.appdb.system_state_repository import SystemStateRepository

_CYCLE_HISTORY_LIMIT = 30


def _cycle_state_keys(user_id: str) -> tuple[str, str]:
    """Return private system-state keys for one account's learning cycles.

    Args:
        user_id: Account that owns the cycle reports.

    Returns:
        Keys for the latest cycle and its history.
    """
    return f"learning.last_cycle:{user_id}", f"learning.cycle_history:{user_id}"


class SqlDailyLearningStorage:
    """Persist daily-learning data with the configured application database."""

    def __init__(self, database: AppDatabase) -> None:
        """Create the adapter for one application database.

        Args:
            database: Database that owns the SQL session lifecycle.
        """
        self._database = database

    def record_observations(
        self, observations: Sequence[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Insert a group of learning observations in one transaction.

        Args:
            observations: Observation fields accepted by the repository.

        Returns:
            The stored observations in input order.
        """
        with self._database.session() as session:
            return [LearningObservationRepository.record(session, **item) for item in observations]

    def save_cycle(self, report: dict[str, Any], user_id: str) -> None:
        """Store the latest cycle and its rolling report history.

        Args:
            report: JSON-serializable cycle report.
            user_id: Account that owns the cycle.
        """
        latest_key, history_key = _cycle_state_keys(user_id)
        with self._database.session() as session:
            SystemStateRepository.set(
                session,
                latest_key,
                report,
                actor="learning_scheduler",
                reason="Daily learning cycle completion",
            )
            history = SystemStateRepository.get_value(session, history_key, [])
            if not isinstance(history, list):
                raise TypeError("Stored daily-learning cycle history must be a list")
            if any(not isinstance(item, dict) for item in history):
                raise TypeError("Stored daily-learning cycle history must contain report objects")
            history.insert(0, report)
            SystemStateRepository.set(
                session,
                history_key,
                history[:_CYCLE_HISTORY_LIMIT],
                actor="learning_scheduler",
                reason="Rolling cycle history update",
            )

    def latest_cycle(self, user_id: str) -> dict[str, Any] | None:
        """Return the latest stored cycle report.

        Returns:
            The latest cycle report, or ``None`` when no cycle has run.
        """
        latest_key, _ = _cycle_state_keys(user_id)
        with self._database.session() as session:
            return SystemStateRepository.get_value(session, latest_key)

    def cycle_history(self, user_id: str, limit: int) -> list[dict[str, Any]]:
        """Return recent stored cycle reports.

        Args:
            limit: Maximum number of reports to return.

        Returns:
            The newest cycle reports, up to ``limit``.
        """
        _, history_key = _cycle_state_keys(user_id)
        with self._database.session() as session:
            history = SystemStateRepository.get_value(session, history_key, [])
            if not isinstance(history, list):
                raise TypeError("Stored daily-learning cycle history must be a list")
            if any(not isinstance(item, dict) for item in history):
                raise TypeError("Stored daily-learning cycle history must contain report objects")
            return history[:limit]
