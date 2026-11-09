"""Backup port adapter backed by the local archive implementation."""

from __future__ import annotations

from pathlib import Path

from forward.core.backup_ports import BackupPort
from forward.infra.backup import backup_now, restore_backup


class LocalBackupAdapter(BackupPort):
    """Connect the backup workflow to the local ZIP archive implementation."""

    def create_backup(
        self, data_root: Path, destination: Path, keep: int, database: Path
    ) -> Path:
        """Write an archive to the configured backup folder.

        Args:
            data_root: Root folder containing application state.
            destination: Folder that stores the archive.
            keep: Number of newest archives to retain.
            database: Configured SQLite database file to include.

        Returns:
            Path to the completed archive.
        """
        return backup_now(data_root, destination, database=database, keep=keep)

    def restore_backup(
        self,
        archive: Path,
        data_root: Path,
        database: Path,
        force: bool,
    ) -> Path:
        """Restore an archive to the configured data and database paths.

        Args:
            archive: ZIP archive to restore.
            data_root: Root folder where saved application state is restored.
            database: Configured SQLite database file to replace.
            force: Whether to move an existing database aside first.

        Returns:
            Path to the restored database file.
        """
        return restore_backup(archive, data_root, database=database, force=force)
