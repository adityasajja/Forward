"""Ports for options dataset."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class OptionsDatasetPort(Protocol):
    """Import, read, and validate historical options datasets."""

    def import_archive(
        self,
        archive: bytes,
        configured_directory: str,
        owner_id: str,
        max_bytes: int,
    ) -> dict[str, Any]:
        """Store and validate a ZIP containing the options data files.

        Args:

            archive: ZIP bytes supplied by the user.

            configured_directory: Configured base folder for imported datasets.

            owner_id: Account that owns the imported dataset.

            max_bytes: Maximum allowed compressed and uncompressed size.

        Returns:

            The saved dataset ID, paths, and row counts.
        """

    def remove_import(self, directory: str) -> None:
        """Remove an import when its metadata could not be stored.

        Args:

            directory: Folder path returned by ``import_archive``.
        """

    def load(self, bars_path: str | Path, instruments_path: str | Path) -> Any:
        """Load bars and their dated instrument catalog.

        Args:

            bars_path: The long-format OHLCV file.

            instruments_path: The matching instrument catalog.

        Returns:

            A feed with validated bars and instrument metadata.
        """

    def inspect(self, bars_path: str | Path, instruments_path: str | Path) -> tuple[int, int]:
        """Validate a dataset and return its bar and symbol counts.

        Args:

            bars_path: The long-format OHLCV file.

            instruments_path: The matching instrument catalog.

        Returns:

            The bar count and unique symbol count.
        """

    def fingerprint(self, bars_path: str | Path, instruments_path: str | Path) -> str:
        """Hash the two files that define one historical dataset.

        Args:

            bars_path: Long-format OHLCV file.

            instruments_path: Matching instrument catalog file.

        Returns:

            Stable SHA-256 fingerprint for both files.
        """
