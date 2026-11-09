"""Storage operations for saved scans and research reports."""

from __future__ import annotations

from typing import Any, Protocol


class SavedScanStoragePort(Protocol):
    """Store saved custom scan definitions."""

    def exists(self) -> bool:
        """Return whether saved scan data exists in storage."""

    def load_all(self) -> list[dict[str, Any]]:
        """Load saved scan definitions.

        Returns:
            Saved scans in their stored order.
        """

    def save_all(self, scans: list[dict[str, Any]]) -> None:
        """Replace all saved scan definitions.

        Args:
            scans: Saved scans in the order they should be stored.
        """


class ValidationReportPort(Protocol):
    """Read named research reports from configured storage."""

    def exists(self, report_name: str) -> bool:
        """Return whether one report file exists.

        Args:
            report_name: Stable name of the report.

        Returns:
            Whether the report is present.
        """

    def read_json(self, report_name: str) -> Any:
        """Read and decode one report.

        Args:
            report_name: Stable name of the report.

        Returns:
            The decoded JSON value.

        Raises:
            OSError: If the report cannot be read.
            ValueError: If the report is not valid JSON.
        """

    def path(self, report_name: str) -> str:
        """Return the configured report path for error messages.

        Args:
            report_name: Stable name of the report.

        Returns:
            The configured path as text.
        """
