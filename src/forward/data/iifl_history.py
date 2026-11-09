"""Daily history readers with explicit IIFL and local-cache sources."""

from __future__ import annotations

import pandas as pd

from forward.data.history import CACHE_ROOT

OHLCV = ["open", "high", "low", "close", "volume"]


def load_daily_from_cache(symbol: str, exchange: str) -> pd.DataFrame:
    """Load daily bars from the maintained local cache.

    Args:
        symbol: Trading symbol.
        exchange: Exchange segment whose cache is read.

    Returns:
        Cached OHLCV bars, or an empty frame when the file is missing.
    """
    path = CACHE_ROOT / exchange.upper() / f"{symbol}.parquet"
    if not path.exists():
        return pd.DataFrame(columns=["ts", *OHLCV])
    frame = pd.read_parquet(path).sort_values("ts").reset_index(drop=True)
    columns = ["ts", *OHLCV] if "ts" in frame.columns else OHLCV
    return frame[columns]
