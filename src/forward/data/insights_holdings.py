"""Read live holdings and saved holdings files for insights and account reports."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from forward.core.insights_ports import (
    InsightsHoldingsPort,
)
from forward.insights.holdings import normalize


class LocalInsightsHoldingsAdapter(InsightsHoldingsPort):
    """Read holdings from one source selected by the caller."""

    def __init__(self, data_root: Path) -> None:
        """Create the adapter.

        Args:
            data_root: Configured root containing insight and portfolio files.
        """
        self._data_root = data_root
        self._snapshot_path = data_root / "insights" / "holdings.json"

    def load(
        self,
        *,
        source: Literal["LIVE", "SNAPSHOT", "EXPORT"],
        client: Any | None = None,
    ) -> dict[str, Any]:
        """Return holdings with their source and last update time.

        Args:
            source: The one configured source to read.
            client: Authenticated broker client when one is available.

        Returns:
            Holdings, their source, and their update time.
        """
        if source == "LIVE":
            if client is None:
                raise ValueError("LIVE holdings source requires an authenticated broker client")
            rows = normalize(client.holdings())
            self._snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            as_of = datetime.now(UTC).isoformat()
            payload = {"as_of": as_of, "holdings": rows}
            self._snapshot_path.write_text(json.dumps(payload), encoding="utf8")
            return {"holdings": rows, "source": "live", "as_of": as_of}
        if source == "SNAPSHOT":
            return self._load_snapshot()
        if source == "EXPORT":
            return self._load_export()
        raise ValueError(f"unsupported holdings source: {source!r}")

    def _load_snapshot(self) -> dict[str, Any]:
        """Read holdings from the selected JSON snapshot.

        Returns:
            Snapshot rows and their recorded source time.

        Raises:
            FileNotFoundError: If no snapshot exists.
            ValueError: If the snapshot has the wrong shape.
        """
        if not self._snapshot_path.exists():
            raise FileNotFoundError(f"holdings snapshot does not exist: {self._snapshot_path}")
        saved = json.loads(self._snapshot_path.read_text(encoding="utf8"))
        if (
            not isinstance(saved, dict)
            or not isinstance(saved.get("holdings"), list)
            or not isinstance(saved.get("as_of"), str)
        ):
            raise ValueError(f"invalid holdings snapshot: {self._snapshot_path}")
        return {
            "holdings": saved["holdings"],
            "source": "snapshot",
            "as_of": saved["as_of"],
        }

    def _load_export(self) -> dict[str, Any]:
        """Read the broker holdings export when it contains valid rows.

        Returns:
            Export holdings and their source time.

        Raises:
            FileNotFoundError: If no export exists.
        """
        path = self._data_root / "portfolio" / "holdings.csv"
        if not path.exists():
            raise FileNotFoundError(f"holdings export does not exist: {path}")
        rows = normalize(pd.read_csv(path).to_dict("records"))
        as_of = datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
        return {"holdings": rows, "source": "export", "as_of": as_of}
