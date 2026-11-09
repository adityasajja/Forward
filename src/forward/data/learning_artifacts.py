"""Read and write local files used by learning reports."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from forward.core.learning_ports import (
    LearningArtifactsPort,
)
from forward.data.sector_catalog import read_sector_table
from forward.research.learning_paper import load_ledger


class LocalLearningArtifactsAdapter(LearningArtifactsPort):
    """Read learning inputs and store report files under one data root.

    Args:
        data_root: Directory that contains the paper ledger and universe files.
    """

    def __init__(self, data_root: Path) -> None:
        self._data_root = data_root

    def paper_ledger(self) -> Any:
        """Read the paper ledger from the configured data root.

        Returns:
            The parsed paper ledger and its source warnings.
        """
        return load_ledger(self._data_root)

    def sector_table(self) -> dict[str, str]:
        """Read the symbol-to-industry map from universe CSV files.

        Returns:
            Industry names keyed by canonical symbol.
        """
        return read_sector_table(self._data_root)

    def write_snapshot(self, payload: Mapping[str, Any]) -> dict[str, str]:
        """Write the latest snapshot and a timestamped copy.

        Args:
            payload: JSON-compatible learning snapshot.

        Returns:
            Paths of the written report and latest snapshot.
        """
        directory = self._data_root / "learning"
        directory.mkdir(parents=True, exist_ok=True)
        stamp = str(payload["generated_at"]).replace(":", "").replace("-", "")
        report_path = directory / f"report_{stamp}.json"
        latest_path = directory / "latest.json"
        text = json.dumps(payload, indent=2, default=str)
        report_path.write_text(text, encoding="utf-8")
        latest_path.write_text(text, encoding="utf-8")
        return {"report": str(report_path), "latest": str(latest_path)}
