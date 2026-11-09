"""Nightly backup of everything that cannot be rebuilt.

The price history is left out on purpose: it can be downloaded again. What is kept is the
configured app database (users, watchlists, paper runs, audit) and the state and study folders
under ``data/``. The database is copied with SQLite's own backup call, because copying the file
while the server is writing to it (WAL mode) can produce a corrupt copy.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime
from pathlib import Path

from loguru import logger

from forward.core.session import IST

#: Durable folders and files under the configured data root. Screenshots and
#: price history are not.
KEEP = (
    "audit",
    "alerts",
    "insights",
    "portfolio",
    "paper_momentum",
    "self_learning",
    "signals",
    "trade_signals",
    "universe",
)
PREFIX = "forward-backup-"
DATABASE_ARCHIVE_NAME = "app.db"


def _copy_database(source: Path, target: Path) -> None:
    """A consistent copy of a live SQLite database."""
    # sqlite3's own context manager commits but does not close, which locks the file on Windows.
    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(target)) as dst:
        src.backup(dst)


def _add_tree(archive: zipfile.ZipFile, root: Path, relative: str) -> None:
    path = root / relative
    if path.is_file():
        archive.write(path, relative)
    elif path.is_dir():
        for file in path.rglob("*"):
            if file.is_file():
                archive.write(file, file.relative_to(root).as_posix())


def prune(directory: Path, keep: int) -> list[Path]:
    """Delete all but the newest ``keep`` backups.

    Args:
        directory: Folder containing backup archives.
        keep: Number of newest archives to retain.

    Returns:
        Paths of removed archives.

    Raises:
        ValueError: If ``keep`` is less than one.
    """
    if keep < 1:
        raise ValueError("keep must be at least one.")
    backups = sorted(directory.glob(f"{PREFIX}*.zip"))
    stale = backups[: max(len(backups) - keep, 0)]
    for path in stale:
        path.unlink(missing_ok=True)
    return stale


def backup_now(
    data_root: Path,
    destination: Path,
    *,
    database: Path,
    keep: int = 14,
    now: datetime | None = None,
) -> Path:
    """Write one dated archive and remove old archives beyond ``keep``.

    Args:
        data_root: Root folder containing durable application state.
        destination: Folder where the archive will be written.
        database: Configured file-backed SQLite database path.
        keep: Number of newest archives to retain.
        now: Archive timestamp, or the current IST time.

    Returns:
        Path to the completed archive.

    Raises:
        FileNotFoundError: If the configured database file is missing.
        ValueError: If ``keep`` is less than one.
    """
    data_root, destination = Path(data_root), Path(destination)
    database = Path(database)
    if keep < 1:
        raise ValueError("keep must be at least one.")
    if not database.is_file():
        raise FileNotFoundError(f"configured SQLite database does not exist: {database}")
    destination.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now(IST)).strftime("%Y%m%d-%H%M%S")
    target = destination / f"{PREFIX}{stamp}.zip"
    partial = target.with_suffix(".zip.partial")

    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as archive:
        copy = Path(tmp) / DATABASE_ARCHIVE_NAME
        _copy_database(database, copy)
        archive.write(copy, DATABASE_ARCHIVE_NAME)
        for name in KEEP:
            _add_tree(archive, data_root, name)
    partial.replace(target)  # a half-written zip never carries the final name

    removed = prune(destination, keep)
    logger.info("backup written: {} ({} old removed)", target.name, len(removed))
    return target


def restore_backup(
    archive_path: Path,
    data_root: Path,
    *,
    database: Path,
    force: bool = False,
) -> Path:
    """Restore durable state and the configured SQLite database.

    Args:
        archive_path: ZIP archive to restore.
        data_root: Root folder where saved application state is restored.
        database: Configured file-backed SQLite database path.
        force: Move an existing database aside before replacing it.

    Returns:
        Path to the restored database file.

    Raises:
        FileNotFoundError: If the archive does not exist.
        FileExistsError: If the database exists and ``force`` is false.
        ValueError: If the archive is invalid or contains unsafe paths.
    """
    archive_path, data_root, database = (
        Path(archive_path),
        Path(data_root),
        Path(database),
    )
    if not archive_path.exists():
        raise FileNotFoundError(f"no such backup: {archive_path}")
    db = database
    with zipfile.ZipFile(archive_path) as archive:
        bad = archive.testzip()
        if bad is not None:
            raise ValueError(f"backup is corrupt (first bad file: {bad})")
        names = archive.namelist()
        if any(n.startswith(("/", "..")) or ".." in Path(n).parts for n in names):
            raise ValueError("backup contains unsafe paths")
        if names.count(DATABASE_ARCHIVE_NAME) != 1:
            raise ValueError("backup must contain exactly one app.db")
        if db.exists() and not force:
            raise FileExistsError(
                f"{db} exists; stop the server and pass force to replace it"
            )
        data_root.mkdir(parents=True, exist_ok=True)
        archive.extractall(
            data_root,
            members=[name for name in names if name != DATABASE_ARCHIVE_NAME],
        )
        db.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=db.parent) as temporary_directory:
            staged_database = Path(temporary_directory) / DATABASE_ARCHIVE_NAME
            with archive.open(DATABASE_ARCHIVE_NAME) as source, staged_database.open("wb") as target:
                shutil.copyfileobj(source, target)
            if db.exists():
                if not force:
                    raise FileExistsError(
                        f"{db} exists; stop the server and pass force to replace it"
                    )
                db.replace(db.with_name(f"{db.name}.before-restore"))
            for suffix in ("-wal", "-shm"):
                stale = db.with_name(db.name + suffix)
                if stale.exists():
                    stale.unlink()
            staged_database.replace(db)
    logger.info("restored {} into {}", archive_path.name, data_root)
    return db
