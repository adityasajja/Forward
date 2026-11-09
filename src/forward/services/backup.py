"""Application service for scheduled state backups."""

from __future__ import annotations

from pathlib import Path

from forward.core.backup_ports import BackupDatabasePort, BackupPort


class BackupService:
    """Create scheduled backups with the configured archive adapter."""

    def __init__(
        self,
        backup: BackupPort,
        *,
        data_root: Path,
        destination: Path,
        keep: int,
        database: BackupDatabasePort,
    ) -> None:
        """Create the backup workflow.

        Args:
            backup: Port that writes and prunes backup archives.
            data_root: Application data directory to archive.
            destination: Directory that stores backup archives.
            keep: Number of newest archives to retain.
            database: Port that exposes the configured SQLite file.
        """
        self._backup = backup
        self._data_root = data_root
        self._destination = destination
        self._keep = keep
        self._database = database

    def run(self) -> Path:
        """Create and return the newest backup archive path."""
        return self._backup.create_backup(
            self._data_root,
            self._destination,
            self._keep,
            self._database.sqlite_file,
        )

    def restore(self, archive: Path, *, force: bool = False) -> Path:
        """Restore an archive through the configured storage adapters.

        Args:
            archive: ZIP archive to restore.
            force: Move the current database aside before replacing it.

        Returns:
            Path to the restored configured database file.

        Raises:
            FileNotFoundError: If the archive does not exist.
            FileExistsError: If the database exists and ``force`` is false.
            NotImplementedError: If the configured database cannot be restored
                by the local archive adapter.
            ValueError: If the archive is invalid or contains unsafe paths.
        """
        return self._backup.restore_backup(
            archive,
            self._data_root,
            self._database.sqlite_file,
            force,
        )
