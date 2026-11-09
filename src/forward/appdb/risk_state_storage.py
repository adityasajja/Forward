"""SQL adapter for durable risk-control values."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from forward.appdb.engine import AppDatabase
from forward.appdb.system_state_repository import SystemStateRepository
from forward.core.risk_ports import (
    RiskStateStoragePort,
)


class SqlRiskStateStorage(RiskStateStoragePort):
    """Read and update risk state in managed database transactions."""

    def __init__(self, database: AppDatabase) -> None:
        """Create the adapter for one application database.

        Args:
            database: Database that owns the SQL session lifecycle.
        """
        self._database = database

    def read(self, defaults: Mapping[str, Any]) -> dict[str, Any]:
        """Read state values in one transaction.

        Args:
            defaults: State names and values used when no row exists.

        Returns:
            A mapping with every requested key and its stored value, or ``None``.
        """
        with self._database.session() as session:
            return {
                key: SystemStateRepository.get_value(session, key, default)
                for key, default in defaults.items()
            }

    def update(
        self,
        defaults: Mapping[str, Any],
        change: Callable[[dict[str, Any]], dict[str, Any]],
        *,
        actor: str,
        reason: str | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Apply one change and its audit record atomically.

        Args:
            defaults: State names and values used when no row exists.
            change: Pure function that returns the values to store.
            actor: User or service making the change.
            reason: Audit reason for the change.

        Returns:
            The values stored before and after the change.
        """
        with self._database.session() as session:
            previous = {
                key: SystemStateRepository.get_value(session, key, default)
                for key, default in defaults.items()
            }
            updates = change(previous)
            if set(updates) - set(defaults):
                raise ValueError("risk state change returned an undeclared key")
            for key, value in updates.items():
                SystemStateRepository.set(
                    session, key, value, actor=actor, reason=reason
                )
            return previous, {**previous, **updates}
