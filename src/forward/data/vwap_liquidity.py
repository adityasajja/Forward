"""Load prior-session liquidity measurements for the VWAP paper strategy."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from forward.core.market_tracking_ports import VwapLiquidityPort


class NseBhavcopyLiquidityAdapter(VwapLiquidityPort):
    """Calculate 20-session liquidity from stored NSE bhavcopy files."""

    def __init__(self, data_root: str | Path) -> None:
        """Create the adapter for the configured historical-data directory.

        Args:
            data_root: Directory that contains the ``nse_bhav`` folder.
        """
        self._directory = Path(data_root) / "nse_bhav"
        self._cached_day: str | None = None
        self._cached_rows: dict[str, dict[str, Any]] = {}

    def liquidity(self, day: date) -> dict[str, dict[str, Any]]:
        """Return the 20 sessions before ``day`` for each eligible stock.

        Args:
            day: Session date that the strategy is evaluating.

        Returns:
            Symbol-keyed average volume, traded value, prior close, and last
            source date. An empty mapping means the complete 20-session sample
            is not available.
        """
        target = day.isoformat()
        if self._cached_day == target:
            return self._cached_rows

        paths = [
            path
            for path in sorted(self._directory.glob("*.parquet"))
            if path.stem < target
        ][-20:]
        if len(paths) < 20:
            return {}

        frames = []
        for path in paths:
            frame = pd.read_parquet(
                path,
                columns=["symbol", "series", "volume", "turnover", "close"],
            )
            frame = frame[frame.series == "EQ"].copy()
            frame["day"] = path.stem
            frames.append(frame)

        rows: dict[str, dict[str, Any]] = {}
        if frames:
            data = pd.concat(frames)
            for symbol, group in data.groupby("symbol"):
                if len(group) != 20 or group.day.nunique() != 20:
                    continue
                rows[f"{symbol}-EQ"] = {
                    "adv": float(group.volume.mean()),
                    # Bhavcopy turnover is stored in lakhs, so convert it to rupees.
                    "value": float(group.turnover.mean()) * 100_000,
                    "previous_close": float(group.iloc[-1].close),
                    "through": group.iloc[-1].day,
                }

        self._cached_day = target
        self._cached_rows = rows
        return rows
