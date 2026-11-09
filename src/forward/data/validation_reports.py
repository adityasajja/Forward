"""Read validation and evidence reports from local JSON files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from forward.core.research_ports import (
    ValidationReportPort,
)

_REPORT_FILES = {
    "paper_validation": Path("self_learning/paper_validation.json"),
    "episodic_validation": Path("self_learning/episodic_pivot_validation.json"),
    "episodic_premise": Path("self_learning/episodic_pivot_premise.json"),
    "alpha_hunt_nifty50": Path("self_learning/alpha_hunt_nifty50.json"),
    "alpha_hunt_midcap150": Path("self_learning/alpha_hunt_midcap150.json"),
    "alpha_hunt_smallcap250": Path("self_learning/alpha_hunt_smallcap250.json"),
    "evidence": Path("signals/evidence.json"),
}


class JsonValidationReportAdapter(ValidationReportPort):
    """Read named report files below one configured data directory."""

    def __init__(self, data_directory: Path) -> None:
        """Create the report adapter.

        Args:
            data_directory: Directory that contains the report subdirectories.
        """
        self._data_directory = data_directory

    def exists(self, report_name: str) -> bool:
        """Return whether one named report exists.

        Args:
            report_name: Stable name from the report catalog.
        """
        return self._report_path(report_name).exists()

    def read_json(self, report_name: str) -> Any:
        """Read and decode one report file.

        Args:
            report_name: Stable name from the report catalog.

        Returns:
            The decoded JSON value.
        """
        return json.loads(self._report_path(report_name).read_text(encoding="utf8"))

    def path(self, report_name: str) -> str:
        """Return the report path as a string for API error details.

        Args:
            report_name: Stable name from the report catalog.
        """
        return str(self._report_path(report_name))

    def _report_path(self, report_name: str) -> Path:
        """Build a path for one catalogued report."""
        return self._data_directory / _REPORT_FILES[report_name]
