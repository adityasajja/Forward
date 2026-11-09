"""Persist trade signal settings and queue records as JSON."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from forward.core.trade_signal_ports import (
    TradeSignalStoragePort,
)


class JsonTradeSignalStorageAdapter(TradeSignalStoragePort):
    """Read and write trade signal data under the configured data directory."""

    def __init__(self, data_root: Path) -> None:
        """Create the adapter.

        Args:
            data_root: Configured directory for application data.
        """
        signal_directory = data_root / "trade_signals"
        self._queue_path = signal_directory / "queue.json"
        self._settings_path = signal_directory / "settings.json"

    def load_queue(self) -> list[dict[str, Any]]:
        """Read queued signal records.

        Returns:
            Saved records, or an empty list when no queue file exists.

        Raises:
            ValueError: If the saved file is not a JSON array.
        """
        if not self._queue_path.exists():
            return []
        records = json.loads(self._queue_path.read_text(encoding="utf-8"))
        if not isinstance(records, list):
            raise ValueError("trade signal queue must be a JSON array")
        return records

    def save_queue(self, records: list[dict[str, Any]]) -> None:
        """Write the current signal records.

        Args:
            records: Serialized queue records.
        """
        self._queue_path.parent.mkdir(parents=True, exist_ok=True)
        self._queue_path.write_text(
            json.dumps(records, indent=2), encoding="utf-8"
        )

    def load_settings(self) -> dict[str, Any] | None:
        """Read the saved signal settings.

        Returns:
            Saved settings, or ``None`` when no settings file exists.

        Raises:
            ValueError: If the saved file is not a JSON object.
        """
        if not self._settings_path.exists():
            return None
        settings = json.loads(self._settings_path.read_text(encoding="utf-8"))
        if not isinstance(settings, dict):
            raise ValueError("trade signal settings must be a JSON object")
        return settings

    def save_settings(self, settings: Mapping[str, Any]) -> None:
        """Write trade signal settings.

        Args:
            settings: JSON-compatible settings.
        """
        self._settings_path.parent.mkdir(parents=True, exist_ok=True)
        self._settings_path.write_text(
            json.dumps(settings, indent=2), encoding="utf-8"
        )
