"""Store day-scoped broker tradebook records as JSON files."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from forward.core.tradebook_ports import DailyTradebookStoragePort


class JsonDailyTradebookAdapter(DailyTradebookStoragePort):
    """Write raw trade and position rows under the portfolio data directory."""

    def __init__(self, data_root: Path) -> None:
        """Create the adapter.

        Args:
            data_root: Directory that contains portfolio data.
        """
        self._root = Path(data_root) / "portfolio"

    def save_trades(self, day: date, rows: list[dict[str, Any]]) -> None:
        """Save one day's raw trades.

        Args:
            day: Market day for these trades.
            rows: Raw broker trade records.
        """
        self._save("tradebook", day, rows)

    def save_positions(self, day: date, rows: list[dict[str, Any]]) -> None:
        """Save one day's raw positions.

        Args:
            day: Market day for these positions.
            rows: Raw broker position records.
        """
        self._save("positions", day, rows)

    def _save(self, folder_name: str, day: date, rows: list[dict[str, Any]]) -> None:
        """Write rows using the existing on-disk response shape.

        Args:
            folder_name: Portfolio folder that stores these records.
            day: Market date encoded in the file name and payload.
            rows: Raw broker records.
        """
        folder = self._root / folder_name
        folder.mkdir(parents=True, exist_ok=True)
        payload = {"date": day.isoformat(), "rows": rows}
        (folder / f"{day.isoformat()}.json").write_text(
            json.dumps(payload, indent=1), encoding="utf8"
        )
