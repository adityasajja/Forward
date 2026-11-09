"""Coordinate options datasets and their backtest or forward-run records."""

from __future__ import annotations

import json
from typing import Any

from forward.core.options_dataset_ports import OptionsDatasetPort
from forward.core.options_run_storage_ports import OptionsRunStoragePort


class OptionsRunService:
    """Coordinate F&O dataset and run operations through outbound ports."""

    def __init__(
        self,
        storage: OptionsRunStoragePort,
        dataset_port: OptionsDatasetPort,
    ) -> None:
        """Create the service with storage and dataset adapters.

        Args:
            storage: Persists datasets and run records.
            dataset_port: Loads and validates option datasets.
        """
        self._storage = storage
        self._dataset_port = dataset_port

    def import_dataset(
        self,
        user_id: str,
        name: str,
        archive: bytes,
        configured_path: str,
        max_bytes: int,
    ) -> dict[str, Any]:
        """Validate, store, and index one options dataset for its owner.

        Args:
            user_id: Account that owns the dataset.
            name: Display name for the imported data.
            archive: ZIP bytes containing the data files.
            configured_path: Absolute or repository-relative storage folder.
            max_bytes: Maximum compressed and uncompressed archive size.

        Returns:
            The saved dataset record.
        """
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("dataset name cannot be blank")

        imported = self._dataset_port.import_archive(
            archive, configured_path, user_id, max_bytes
        )
        record = {
            "dataset_id": imported["dataset_id"],
            "user_id": user_id,
            "name": clean_name,
            "bars_path": str(imported["bars_path"]),
            "instruments_path": str(imported["instruments_path"]),
            "bar_count": imported["bar_count"],
            "symbol_count": imported["symbol_count"],
        }
        try:
            self._storage.register_dataset(record)
        except Exception:
            self._dataset_port.remove_import(imported["directory"])
            raise
        return record

    def dataset(self, user_id: str, dataset_id: str) -> dict[str, Any] | None:
        """Return an imported dataset only when it belongs to ``user_id``."""
        return self._storage.dataset(user_id, dataset_id)

    def list_datasets(self, user_id: str) -> list[dict[str, Any]]:
        """List one owner's imported datasets, newest first."""
        return self._storage.list_datasets(user_id)

    def create_backtest(self, values: dict[str, Any]) -> None:
        """Create a queued backtest record."""
        self._storage.create_backtest(values)

    def update_backtest(self, run_id: str, user_id: str, **values: Any) -> None:
        """Update a backtest record owned by ``user_id``."""
        self._storage.update_backtest(run_id, user_id, values)

    def backtest(self, run_id: str, user_id: str) -> dict[str, Any] | None:
        """Read a backtest record only for its owner."""
        row = self._storage.backtest(run_id, user_id)
        return self._decode_run(row, "result")

    def create_forward_run(self, values: dict[str, Any]) -> None:
        """Create a forward-paper run record."""
        self._storage.create_forward_run(values)

    def update_forward_run(self, run_id: str, user_id: str, **values: Any) -> None:
        """Update a forward-paper record owned by ``user_id``."""
        self._storage.update_forward_run(run_id, user_id, values)

    def forward_run(self, run_id: str, user_id: str) -> dict[str, Any] | None:
        """Read a forward-paper run only for its owner."""
        row = self._storage.forward_run(run_id, user_id)
        return self._decode_run(row, "snapshot")

    def list_forward_runs(self, user_id: str, limit: int) -> list[dict[str, Any]]:
        """List recent forward-paper runs for one owner."""
        rows = self._storage.list_forward_runs(user_id, limit)
        return [self._decode_run(row, "snapshot") for row in rows]

    def owns_forward_run(self, run_id: str, user_id: str) -> bool:
        """Check whether a forward-paper run belongs to an account."""
        return self._storage.forward_run(run_id, user_id) is not None

    @staticmethod
    def _decode_run(row: dict[str, Any] | None, payload_key: str) -> dict[str, Any] | None:
        """Decode JSON fields in one stored run row.

        Args:
            row: The stored row, or ``None`` when the caller does not own it.
            payload_key: The field containing the result or snapshot JSON.

        Returns:
            A copied row with decoded JSON fields, or ``None``.
        """
        if row is None:
            return None
        result = dict(row)
        result["config"] = json.loads(result["config"])
        if result.get(payload_key):
            result[payload_key] = json.loads(result[payload_key])
        return result
