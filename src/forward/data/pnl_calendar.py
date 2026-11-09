"""Read and write local files used by the P&L calendar."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any

import pandas as pd

from forward.core.pnl_ports import (
    PnlCalendarDataPort,
)
from forward.data.history import DAILY_DIR


class FilePnlCalendarAdapter(PnlCalendarDataPort):
    """Load P&L calendar files and holding prices from the configured data root."""

    def __init__(self, data_root: Path) -> None:
        """Create the adapter.

        Args:
            data_root: Directory that contains portfolio and research data.
        """
        self._data_root = Path(data_root)
        self._portfolio_root = self._data_root / "portfolio"

    def paper_momentum_settlements(self) -> list[dict[str, Any]]:
        """Read valid settlement rows from the weekly paper ledger.

        Returns:
            Settlement records in file order.
        """
        path = self._data_root / "paper_momentum" / "settlements.jsonl"
        if not path.exists():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf8").splitlines()
            if line.strip()
        ]

    def gap_plan_trades(self) -> list[dict[str, Any]]:
        """Read saved trades from the gap plan."""
        path = self._data_root / "research" / "gap_plan.json"
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf8"))["trades"]

    def official_gl_summary(self) -> dict[str, Any]:
        """Read the broker's official realized gain and loss report."""
        path = self._portfolio_root / "gl_summary.json"
        try:
            return json.loads(path.read_text(encoding="utf8"))
        except (OSError, ValueError):
            return {}

    def manual_realised_entries(self, user_id: str) -> dict[str, dict[str, Any]]:
        """Read manual daily trading profit entries."""
        path = self._manual_realised_path(user_id)
        if not path.exists():
            return {}
        entries = json.loads(path.read_text(encoding="utf8"))
        if not isinstance(entries, dict) or any(
            not isinstance(value, dict) for value in entries.values()
        ):
            raise ValueError(f"manual realised P&L file must contain date records: {path}")
        return entries

    def save_manual_realised_entries(
        self, user_id: str, entries: dict[str, dict[str, Any]]
    ) -> None:
        """Save manual daily trading profit entries."""
        self._write(self._manual_realised_path(user_id), entries)

    def _manual_realised_path(self, user_id: str) -> Path:
        """Return the path for one account's manual realised entries.

        Args:
            user_id: Account that owns the entries.

        Returns:
            A path whose filename does not expose or interpret the account ID.
        """
        user_key = sha256(user_id.encode("utf8")).hexdigest()
        return self._portfolio_root / "realised_pnl" / f"{user_key}.json"

    def stored_pnl_days(self) -> dict[str, dict[str, Any]]:
        """Read stored daily holdings P&L records."""
        path = self._portfolio_root / "pnl_daily.json"
        try:
            return json.loads(path.read_text(encoding="utf8")).get("days", {})
        except (OSError, ValueError):
            return {}

    def save_pnl_days(
        self, days: dict[str, dict[str, Any]], updated_at: str
    ) -> None:
        """Save daily holdings P&L records."""
        self._write(
            self._portfolio_root / "pnl_daily.json",
            {"updated": updated_at, "days": days},
        )

    def daily_closes(self, symbol: str) -> pd.Series | None:
        """Read cached daily closes for a holding.

        Args:
            symbol: Trading symbol to read.

        Returns:
            Close prices indexed by trading date, or ``None`` when no cache exists.
        """
        path = self._data_root / DAILY_DIR / "NSEEQ" / f"{symbol}.parquet"
        if not path.exists():
            return None
        frame = pd.read_parquet(path, columns=["ts", "close"])
        if frame[["ts", "close"]].isna().any().any():
            raise ValueError(f"broker history contains missing dates or closes: {path}")
        if not (frame["close"] > 0).all():
            raise ValueError(f"broker history contains a non-positive close: {path}")
        return pd.Series(
            frame["close"].to_numpy(float),
            index=pd.DatetimeIndex(frame["ts"]).normalize(),
        )

    @staticmethod
    def _write(path: Path, payload: Any) -> None:
        """Write JSON atomically so an interrupted write cannot truncate a file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=1), encoding="utf8")
        temporary.replace(path)
