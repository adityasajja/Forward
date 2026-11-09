"""SQL adapter for options run storage."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from forward.appdb.engine import AppDatabase
from forward.appdb.options_repository import OptionsRunRepository


class SqlOptionsRunStorage:
    """Run options repository operations inside database sessions."""

    def __init__(self, db: AppDatabase) -> None:
        """Create the storage adapter.

        Args:
            db: The application database connection manager.
        """
        self._db = db

    def _call(self, operation: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Run one repository operation in a managed database session."""
        with self._db.session() as session:
            return operation(session, *args, **kwargs)

    def register_dataset(self, values: dict[str, Any]) -> Any:
        """Store one dataset row."""
        return self._call(OptionsRunRepository.register_dataset, values)

    def dataset(self, user_id: str, dataset_id: str) -> dict[str, Any] | None:
        """Read one dataset owned by the caller."""
        return self._call(OptionsRunRepository.dataset, user_id, dataset_id)

    def list_datasets(self, user_id: str) -> list[dict[str, Any]]:
        """List datasets owned by the caller."""
        return self._call(OptionsRunRepository.list_datasets, user_id)

    def create_backtest(self, values: dict[str, Any]) -> Any:
        """Create one backtest record."""
        return self._call(OptionsRunRepository.create_backtest, values)

    def update_backtest(self, run_id: str, user_id: str, values: dict[str, Any]) -> Any:
        """Update one backtest owned by the caller."""
        return self._call(OptionsRunRepository.update_backtest, run_id, user_id, **values)

    def backtest(self, run_id: str, user_id: str) -> dict[str, Any] | None:
        """Read one backtest owned by the caller."""
        return self._call(OptionsRunRepository.backtest, run_id, user_id)

    def create_forward_run(self, values: dict[str, Any]) -> Any:
        """Create one forward-run record."""
        return self._call(OptionsRunRepository.create_forward_run, values)

    def update_forward_run(self, run_id: str, user_id: str, values: dict[str, Any]) -> Any:
        """Update one forward run owned by the caller."""
        return self._call(OptionsRunRepository.update_forward_run, run_id, user_id, **values)

    def forward_run(self, run_id: str, user_id: str) -> dict[str, Any] | None:
        """Read one forward run owned by the caller."""
        return self._call(OptionsRunRepository.forward_run, run_id, user_id)

    def list_forward_runs(self, user_id: str, limit: int) -> list[dict[str, Any]]:
        """List a caller's recent forward runs."""
        return self._call(OptionsRunRepository.list_forward_runs, user_id, limit)
