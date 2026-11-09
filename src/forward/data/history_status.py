"""Daily history cache status adapter."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any


class CachedDailyHistoryStatusAdapter:
    """Summarize cached daily bars by exchange."""

    def __init__(self, cache_root: Path) -> None:
        """Create the cache status adapter.

        Args:
            cache_root: Directory containing one folder per exchange.
        """
        self._cache_root = Path(cache_root)

    def status(self) -> dict[str, Any]:
        """Return daily cache coverage using the existing API field names."""
        exchanges = []
        if self._cache_root.is_dir():
            folders = sorted(path for path in self._cache_root.iterdir() if path.is_dir())
            for folder in folders:
                files = list(folder.glob("*.parquet"))
                if not files:
                    continue
                stats = [path.stat() for path in files]
                exchanges.append(
                    {
                        "exchange": folder.name,
                        "symbols": len(files),
                        "size_mb": round(sum(item.st_size for item in stats) / 1e6, 1),
                        "last_sync": datetime.fromtimestamp(
                            max(item.st_mtime for item in stats)
                        ).isoformat(),
                    }
                )
        return {
            "cache_root": str(self._cache_root),
            "exchanges": exchanges,
            "total_symbols": sum(item["symbols"] for item in exchanges),
        }
