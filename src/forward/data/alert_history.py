"""Daily-bar adapter for alert indicator context."""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

from forward.core.market_models import Bar
from forward.data.panel import frames


class CachedAlertHistoryAdapter:
    """Read daily bars from the configured local exchange caches."""

    def __init__(self, cache_root: Path) -> None:
        """Create the adapter for the daily-bar cache root.

        Args:
            cache_root: Directory containing one subdirectory per exchange.
        """
        self._cache_root = Path(cache_root)

    def context(self, symbol: str, current_bar: Bar) -> pd.DataFrame | None:
        """Merge the observed current-session bar into cached daily history.

        Args:
            symbol: Market symbol used as the parquet file stem.
            current_bar: Observed OHLCV data for the current market session.

        Returns:
            A sorted daily frame with the forming bar, or ``None`` when no cached
            daily bars exist for the symbol.
        """
        if not self._cache_root.exists():
            return None
        matches = [
            frame
            for directory in sorted(path for path in self._cache_root.iterdir() if path.is_dir())
            if (frame := frames(directory).get(symbol)) is not None and not frame.empty
        ]
        if len(matches) > 1:
            raise ValueError(f"{symbol}: daily history exists in multiple exchange caches")
        if matches:
            frame = matches[0].sort_values("ts").reset_index(drop=True).copy()
            current_timestamp = pd.Timestamp(current_bar.ts)
            if pd.isna(current_timestamp):
                raise ValueError(f"{symbol}: current bar needs a valid timestamp")
            if current_timestamp.tzinfo is not None:
                current_timestamp = current_timestamp.tz_convert("Asia/Kolkata").tz_localize(None)
            row = {
                "ts": current_timestamp,
                "open": current_bar.open,
                "high": current_bar.high,
                "low": current_bar.low,
                "close": current_bar.close,
                "volume": current_bar.volume,
            }
            prices = (row["open"], row["high"], row["low"], row["close"])
            if not all(math.isfinite(value) and value > 0 for value in prices):
                raise ValueError(f"{symbol}: current bar needs positive OHLC prices")
            if row["high"] < max(row["open"], row["close"]) or row["low"] > min(
                row["open"], row["close"]
            ):
                raise ValueError(f"{symbol}: current bar has inconsistent high and low prices")
            if row["volume"] is not None and (
                not math.isfinite(row["volume"]) or row["volume"] < 0
            ):
                raise ValueError(f"{symbol}: current bar volume must be non-negative or unknown")

            latest_day = pd.Timestamp(frame["ts"].iloc[-1]).date()
            current_day = row["ts"].date()
            if current_day < latest_day:
                raise ValueError(f"{symbol}: current bar is older than cached history")
            if current_day == latest_day:
                for column, value in row.items():
                    frame.at[frame.index[-1], column] = value
            else:
                frame = pd.concat([frame, pd.DataFrame([row])], ignore_index=True)
            return frame
        return None
