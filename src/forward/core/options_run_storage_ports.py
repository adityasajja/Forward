"""Ports for options run storage."""

from __future__ import annotations

from typing import Any, Protocol


class OptionsRunStoragePort(Protocol):
    """Store user-owned options datasets and run records."""

    def register_dataset(self, values: dict[str, Any]) -> Any:
        """Insert dataset metadata."""

    def dataset(self, user_id: str, dataset_id: str) -> dict[str, Any] | None:
        """Read a dataset owned by the user."""

    def list_datasets(self, user_id: str) -> list[dict[str, Any]]:
        """List datasets owned by the user."""

    def create_backtest(self, values: dict[str, Any]) -> Any:
        """Insert a backtest record."""

    def update_backtest(self, run_id: str, user_id: str, values: dict[str, Any]) -> Any:
        """Update a backtest record owned by the user."""

    def backtest(self, run_id: str, user_id: str) -> dict[str, Any] | None:
        """Read a backtest record owned by the user."""

    def create_forward_run(self, values: dict[str, Any]) -> Any:
        """Insert a forward-run record."""

    def update_forward_run(self, run_id: str, user_id: str, values: dict[str, Any]) -> Any:
        """Update a forward-run record owned by the user."""

    def forward_run(self, run_id: str, user_id: str) -> dict[str, Any] | None:
        """Read a forward-run record owned by the user."""

    def list_forward_runs(self, user_id: str, limit: int) -> list[dict[str, Any]]:
        """List recent forward-run records owned by the user."""
