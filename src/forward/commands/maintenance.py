"""CLI commands for backups and market-data checks."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path


def run_backup(args: Namespace) -> int:
    """Create an application backup.

    Args:
        args: Parsed command-line options for the backup command.

    Returns:
        The process exit code.
    """
    from forward.composition import get_backup_service

    try:
        print(get_backup_service().run())
    except (OSError, NotImplementedError, ValueError) as exc:
        print(f"backup failed: {exc}")
        return 1
    return 0


def run_data_check(args: Namespace) -> int:
    """Check local market data for gaps, jumps, and unreadable files.

    Args:
        args: Parsed command-line options for the data-check command.

    Returns:
        Zero when the data is clean, or one when problems are found.
    """
    from forward.composition import get_daily_market_maintenance_service
    from forward.config.settings import research_data_root

    report = get_daily_market_maintenance_service().check_data(research_data_root())
    print(json.dumps(report, indent=2, default=str))
    return 1 if any(report.get(key) for key in ("jumps", "gaps", "unreadable")) else 0


def run_restore(args: Namespace) -> int:
    """Restore application data from an archive.

    Args:
        args: Parsed command-line options for the restore command.

    Returns:
        The process exit code.
    """
    from forward.composition import get_backup_service

    try:
        print(get_backup_service().restore(Path(args.archive), force=args.force))
    except (OSError, ValueError, NotImplementedError) as exc:
        print(f"restore failed: {exc}")
        return 1
    return 0
