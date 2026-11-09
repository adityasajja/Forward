"""SQL adapter for the application's durable key/value state."""

from __future__ import annotations

from typing import Any

from forward.appdb.engine import AppDatabase
from forward.appdb.system_state_repository import SystemStateRepository


class SqlSystemStateStorage:
    """Store application state in the configured SQL database."""

    def __init__(self, database: AppDatabase) -> None:
        """Create the adapter for one application database.

        Args:
            database: Database that owns the SQL session lifecycle.
        """
        self._database = database

    def get(self, key: str) -> Any | None:
        """Read the value stored for a key.

        Args:
            key: Stable name for one state value.

        Returns:
            The stored value, or ``None`` when no value exists.
        """
        with self._database.session() as session:
            return SystemStateRepository.get_value(session, key)

    def set(self, key: str, value: Any, *, actor: str, reason: str) -> None:
        """Write one state value and its audit context.

        Args:
            key: Stable name for the value.
            value: JSON-serializable state.
            actor: User or system component making the change.
            reason: Human-readable reason recorded with the change.
        """
        with self._database.session() as session:
            SystemStateRepository.set(
                session, key, value, actor=actor, reason=reason
            )
