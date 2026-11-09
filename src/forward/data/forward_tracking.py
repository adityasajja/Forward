"""File-backed adapter for dashboard forward-research summaries."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from forward.core.forward_tracking_ports import (
    ForwardTrackingPort,
)


class FileForwardTrackingAdapter(ForwardTrackingPort):
    """Read saved research state from its configured data directory."""

    def __init__(self, data_root: Path) -> None:
        """Create the file-backed adapter.

        Args:
            data_root: Directory that contains the research state files.
        """
        self._data_root = data_root

    def oversold_volatile_state(self) -> dict[str, Any]:
        """Read saved oversold-and-volatile signals.

        Returns:
            The saved tracker state, or an empty mapping before the first run.
        """
        return self._read("forward_oversold_volatile.json")

    def gap_plan_state(self) -> dict[str, Any]:
        """Read saved gap-plan trades and market state.

        Returns:
            The saved plan state, or an empty mapping before the first run.
        """
        return self._read("gap_plan.json")

    def _read(self, filename: str) -> dict[str, Any]:
        """Read one JSON state file from the research data directory.

        Args:
            filename: Name of the state file inside the research directory.

        Returns:
            The decoded state, or an empty mapping when no run has created it.

        Raises:
            json.JSONDecodeError: If the saved file does not contain valid JSON.
            ValueError: If the decoded JSON value is not an object.
        """
        path = self._data_root / "research" / filename
        if not path.exists():
            return {}
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise ValueError(f"research state must be a JSON object: {path}")
        return state
