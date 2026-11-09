"""Store self-learning state in the configured research-data directory."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from forward.core.self_learning_ports import SelfLearningStateStoragePort


class JsonSelfLearningStateAdapter(SelfLearningStateStoragePort):
    """Read and write one JSON state file.

    Args:
        path: Configured location of the self-learning state file.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    def load_state(self) -> Mapping[str, Any] | None:
        """Load saved engine state.

        Returns:
            Saved state, or ``None`` when the file does not exist.

        Raises:
            json.JSONDecodeError: If the file does not contain valid JSON.
        """
        if not self._path.exists():
            return None
        return json.loads(self._path.read_text(encoding="utf-8"))

    def save_state(self, state: Mapping[str, Any]) -> None:
        """Write the current engine state.

        Args:
            state: Serializable engine state.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(dict(state), indent=2), encoding="utf-8")
