"""Store custom scan definitions in a local JSON file."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from forward.core.research_ports import (
    SavedScanStoragePort,
)


class JsonSavedScanStorageAdapter(SavedScanStoragePort):
    """Read and replace saved scan definitions at one configured path."""

    def __init__(self, path: Path) -> None:
        """Create the storage adapter.

        Args:
            path: File that stores the user's scan definitions.
        """
        self._path = path

    def exists(self) -> bool:
        """Return whether the saved scan file exists."""
        return self._path.exists()

    def load_all(self) -> list[dict[str, Any]]:
        """Read saved scans from the configured JSON file.

        Returns:
            Saved scans in their stored order.
        """
        return json.loads(self._path.read_text(encoding="utf-8"))

    def save_all(self, scans: list[dict[str, Any]]) -> None:
        """Write all saved scans to the configured JSON file.

        Args:
            scans: Saved scans in the order they should be stored.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(scans, indent=2), encoding="utf-8")
