"""Ports for creating application state backups."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class BackupDatabasePort(Protocol):
    """Expose the configured file needed by the local backup adapter."""

    @property
    def sqlite_file(self) -> Path:
        """Return the configured SQLite file.

        Raises:
            NotImplementedError: If the configured database is not file-backed
                SQLite.
        """


class BackupPort(Protocol):
    """Write one dated archive of the application's durable state."""

    def create_backup(
        self, data_root: Path, destination: Path, keep: int, database: Path
    ) -> Path:
        """Create one archive and prune older archives.

        Args:
            data_root: Root folder containing application state.
            destination: Folder that stores the archive.
            keep: Number of newest archives to retain.
            database: Configured SQLite database file to include.

        Returns:
            Path to the completed archive.
        """

    def restore_backup(
        self,
        archive: Path,
        data_root: Path,
        database: Path,
        force: bool,
    ) -> Path:
        """Restore one archive to its configured state and database paths.

        Args:
            archive: ZIP archive to restore.
            data_root: Root folder where saved application state is restored.
            database: Configured SQLite database file to replace.
            force: Whether to move an existing database aside first.

        Returns:
            Path to the restored database file.
        """
