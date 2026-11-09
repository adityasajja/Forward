"""Read market-data freshness facts from the local cache."""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path

import pandas as pd

from forward.core.market_models import MarketDataStatusSnapshot
from forward.data.history import DAILY_DIR
from forward.data.indices import NIFTY_50, index_path
from forward.data.quality import read as read_quality


@lru_cache(maxsize=128)
def _last_day_version(path: Path, modified_ns: int, size: int) -> date | None:
    """Read and cache one parquet file's latest session date.

    Args:
        path: Parquet file to read.
        modified_ns: File modification time used to expire old results.
        size: File size used to expire old results.

    Returns:
        Latest session date, or ``None`` for an empty file.
    """
    frame = pd.read_parquet(path, columns=["ts"])
    return None if frame.empty else pd.to_datetime(frame["ts"]).max().date()


def _last_day(path: Path) -> date | None:
    """Return the latest date from one cache file.

    Args:
        path: Parquet cache file to inspect.

    Returns:
        Latest session date, or ``None`` when the file has no rows.
    """
    stat = path.stat()
    return _last_day_version(path, stat.st_mtime_ns, stat.st_size)


def _source(path: Path) -> str | None:
    """Read an index source label when it was recorded.

    Args:
        path: Index parquet path.

    Returns:
        The recorded source label, or ``None`` when no label exists.
    """
    source_path = path.with_suffix(".source")
    if not source_path.exists():
        return None
    source = source_path.read_text(encoding="utf8").strip()
    if not source:
        raise ValueError(f"index source label is empty: {source_path}")
    return source


class CachedMarketDataStatusAdapter:
    """Read freshness metadata from the configured data directory."""

    def __init__(self, data_root: Path) -> None:
        """Create the adapter.

        Args:
            data_root: Directory that contains cached market data.
        """
        self._data_root = Path(data_root)

    def snapshot(self) -> MarketDataStatusSnapshot:
        """Read latest dates and metadata for the app's freshness report.

        Returns:
            Stored dates and metadata used by the freshness service.
        """
        stock_root = self._data_root / DAILY_DIR / "NSEEQ"
        stock_files = sorted(stock_root.glob("*.parquet"))
        stock_dates = [day for day in (_last_day(path) for path in stock_files) if day]

        index_file = index_path(self._data_root, NIFTY_50)
        index_as_of = _last_day(index_file) if index_file.exists() else None
        quality = read_quality(self._data_root)
        return MarketDataStatusSnapshot(
            stock_as_of=max(stock_dates) if stock_dates else None,
            index_as_of=index_as_of,
            index_origin=_source(index_file) if index_file.exists() else None,
            quality=quality,
            sample_count=len(stock_files),
            demo=(self._data_root / ".demo").exists(),
        )
