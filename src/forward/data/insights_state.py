"""Store insight settings and delivery state in the configured data folder."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from forward.core.insights_ports import (
    InsightsStateStoragePort,
)


class JsonInsightsStateAdapter(InsightsStateStoragePort):
    """Read and write insight settings and delivery state as JSON files.

    Args:
        data_root: Configured root that contains the insights directory.
    """

    def __init__(self, data_root: Path) -> None:
        self._directory = data_root / "insights"

    def load_settings(self) -> Mapping[str, Any] | None:
        """Load saved insight settings.

        Returns:
            Saved settings, or None when the settings file does not exist.
        """
        path = self._directory / "settings.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def save_settings(self, settings: Mapping[str, Any]) -> None:
        """Write insight settings.

        Args:
            settings: Settings fields that can be encoded as JSON.
        """
        self._directory.mkdir(parents=True, exist_ok=True)
        path = self._directory / "settings.json"
        path.write_text(json.dumps(dict(settings), indent=2), encoding="utf-8")

    def load_state(self) -> Mapping[str, Any]:
        """Load delivery and comparison state.

        Returns:
            Saved state, or an empty mapping when the file does not exist.
        """
        path = self._directory / "state.json"
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def save_state(self, state: Mapping[str, Any]) -> None:
        """Write delivery and comparison state.

        Args:
            state: State fields that can be encoded as JSON.
        """
        self._directory.mkdir(parents=True, exist_ok=True)
        path = self._directory / "state.json"
        path.write_text(json.dumps(dict(state)), encoding="utf-8")
