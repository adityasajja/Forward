"""Select local market-data universes for research and validation."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import polars as pl

from forward.data.history import CACHE_ROOT


def liquid_universe(
    exchange: str = "NSEEQ",
    limit: int = 120,
    min_bars: int = 150,
) -> list[str]:
    """Select cached symbols by median daily turnover.

    Screening uses current turnover, so a historical run can include look-ahead
    bias. Use a point-in-time universe for a result that must avoid that bias.

    Args:
        exchange: Exchange segment whose cached bars are read.
        limit: Maximum number of symbols returned.
        min_bars: Minimum cached bars required per symbol.

    Returns:
        Symbols sorted from highest to lowest median turnover.
    """
    paths = sorted((CACHE_ROOT / exchange.upper()).glob("*.parquet"))

    def score(path: Path) -> tuple[float, str] | None:
        try:
            frame = pl.read_parquet(path, columns=["close", "volume"])
        except Exception:
            return None
        if len(frame) < min_bars:
            return None
        turnover = float(
            np.nanmedian(frame["close"].to_numpy() * frame["volume"].to_numpy())
        )
        if not np.isfinite(turnover) or turnover <= 0:
            return None
        return turnover, path.stem.upper()

    with ThreadPoolExecutor(max_workers=8) as pool:
        scored = [item for item in pool.map(score, paths) if item is not None]
    scored.sort(reverse=True)
    return [symbol for _, symbol in scored[:limit]]
