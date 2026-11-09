"""Store computed market-intelligence snapshots on the local disk."""

from __future__ import annotations

import logging
import os
import pickle
from pathlib import Path
from typing import Any

from forward.core.market_intel_ports import (
    MarketIntelSnapshotCachePort,
)

logger = logging.getLogger("forward.market_intel")


class PickleMarketIntelSnapshotAdapter(MarketIntelSnapshotCachePort):
    """Cache one computed snapshot in a local pickle file.

    Args:
        path: File used to store the latest fingerprint and snapshot.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self, fingerprint: str) -> Any | None:
        """Return the stored snapshot when its fingerprint still matches.

        Args:
            fingerprint: Identity of the data and model configuration.

        Returns:
            The matching snapshot, or None when the cache cannot be used.
        """
        try:
            with self._path.open("rb") as snapshot_file:
                stored_fingerprint, snapshot = pickle.load(snapshot_file)
            if stored_fingerprint == fingerprint:
                return snapshot
        except Exception:
            return None
        return None

    def save(self, fingerprint: str, snapshot: Any) -> None:
        """Replace the cached snapshot after writing a complete temporary file.

        Args:
            fingerprint: Identity of the data and model configuration.
            snapshot: Snapshot returned by the market-intelligence calculation.
        """
        temporary_path = self._path.with_suffix(".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with temporary_path.open("wb") as snapshot_file:
                pickle.dump(
                    (fingerprint, snapshot),
                    snapshot_file,
                    protocol=pickle.HIGHEST_PROTOCOL,
                )
            os.replace(temporary_path, self._path)
        except Exception as exc:
            logger.debug("market intel result not stored: %s", exc)
