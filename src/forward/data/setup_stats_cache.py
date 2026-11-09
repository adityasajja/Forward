"""Persist signal setup statistics under the configured data directory."""

from __future__ import annotations

import os
import pickle
from typing import Any

from forward.config.settings import research_data_root
from forward.core.learning_ports import (
    SetupStatsCachePort,
)


class PickleSetupStatsCacheAdapter(SetupStatsCachePort):
    """Store one keyed setup-statistics snapshot as a pickle file."""

    def __init__(self) -> None:
        """Create the adapter at the configured setup-statistics path."""
        self._path = research_data_root() / "panel" / "setup_stats.pkl"

    def load(self) -> tuple[str, dict[str, Any]] | None:
        """Read the saved key and report.

        Returns:
            The input key and statistics, or ``None`` when no snapshot exists.
        """
        if not self._path.exists():
            return None
        with self._path.open("rb") as file:
            input_key, statistics = pickle.load(file)
        return input_key, statistics

    def save(self, input_key: str, statistics: dict[str, Any]) -> None:
        """Write statistics atomically so an interrupted write is not visible.

        Args:
            input_key: Hash of the settings and market data used in the report.
            statistics: Report keyed by setup name.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self._path.with_suffix(self._path.suffix + ".tmp")
        with temporary_path.open("wb") as file:
            pickle.dump((input_key, statistics), file, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(temporary_path, self._path)
