"""IIFL contract cache status adapter."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from forward.brokers.iifl.contracts import CACHE_DIR


class IiflContractCacheStatusAdapter:
    """Summarize locally cached IIFL contract files."""

    def __init__(self, cache_dir: Path = CACHE_DIR) -> None:
        """Create the contract cache status adapter.

        Args:
            cache_dir: Directory containing one JSON file per segment.
        """
        self._cache_dir = Path(cache_dir)

    def status(self) -> dict[str, Any]:
        """Return contract cache coverage using the existing API field names."""
        segments = []
        if self._cache_dir.is_dir():
            for path in sorted(self._cache_dir.glob("*.json")):
                stat = path.stat()
                count = self._contract_count(path)
                segments.append(
                    {
                        "segment": path.stem,
                        "contracts": count,
                        "size_mb": round(stat.st_size / 1e6, 2),
                        "synced_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    }
                )
        return {
            "cache_dir": str(self._cache_dir),
            "segments": segments,
            "total_contracts": sum(item["contracts"] for item in segments),
        }

    @staticmethod
    def _contract_count(path: Path) -> int:
        """Count rows in an IIFL contract file or reject its invalid shape."""
        with path.open(encoding="utf-8") as source:
            payload = json.load(source)
        if not isinstance(payload, list):
            raise ValueError(f"IIFL contract file {path} must contain a row list")
        return len(payload)
