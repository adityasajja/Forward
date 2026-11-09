"""Feed abstractions.

A feed converts some source of truth into an ordered sequence of
:class:`MarketSnapshot`. The backtester only ever consumes snapshots, which
keeps it completely decoupled from where data came from — CSV, TimescaleDB,
or IBKR.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

import numpy as np
import polars as pl

from forward.core.enums import Timeframe
from forward.core.feeds import snapshot_rows
from forward.core.instrument_models import Instrument
from forward.core.market_models import Bar, MarketSnapshot


class DataFeed(ABC):
    """Produces market data for backtests."""

    @abstractmethod
    def load(self) -> list[MarketSnapshot]:
        """Return snapshots sorted ascending by timestamp."""

    @property
    @abstractmethod
    def instruments(self) -> dict[str, Instrument]:
        ...

    def to_frame(self) -> pl.DataFrame:
        """Return feed snapshots as a sorted long-format dataframe."""
        return snapshots_to_frame(self.load())


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def snapshots_to_frame(snapshots: list[MarketSnapshot]) -> pl.DataFrame:
    """Convert snapshots to a sorted Polars dataframe.

    Args:
        snapshots: Market observations with per-symbol bars.

    Returns:
        A long dataframe with timestamp, symbol, OHLC, and volume columns.
    """
    rows = snapshot_rows(snapshots)
    if not rows:
        return pl.DataFrame(
            schema={
                "ts": pl.Datetime,
                "symbol": pl.Utf8,
                "open": pl.Float64,
                "high": pl.Float64,
                "low": pl.Float64,
                "close": pl.Float64,
                "volume": pl.Float64,
            }
        )
    return pl.DataFrame(rows).sort(["ts", "symbol"])


def pivot_to_snapshots(df: pl.DataFrame, timeframe: Timeframe) -> list[MarketSnapshot]:
    """Convert a long DataFrame (ts, symbol, ohlcv) into ordered snapshots.

    This is the hot path for every backtest — a full-market run pushes millions
    of bars through it — so it stays on numpy arrays and makes a single pass.
    Grouping is done by walking the sorted timestamps rather than a per-row
    group-by, which builds one group object per bar and dominated total
    runtime (measured ~40s of a 69s backtest).

    Raises:
        ValueError: If timestamps, symbols, or OHLC prices are incomplete or invalid.
    """
    required = {"ts", "symbol", "open", "high", "low", "close"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"DataFrame missing required columns: {sorted(missing)}")

    if df.is_empty():
        return []
    if df["ts"].null_count() > 0 or df["symbol"].null_count() > 0:
        raise ValueError("market rows need timestamps and symbols")

    # Stable sort keeps the original symbol ordering within a timestamp.
    work = df.sort("ts", maintain_order=True)
    n = work.height

    symbols = work["symbol"].to_numpy()
    opens = work["open"].to_numpy().astype(float)
    highs = work["high"].to_numpy().astype(float)
    lows = work["low"].to_numpy().astype(float)
    closes = work["close"].to_numpy().astype(float)
    for name, values in (
        ("open", opens),
        ("high", highs),
        ("low", lows),
        ("close", closes),
    ):
        if not np.isfinite(values).all() or (values <= 0).any():
            raise ValueError(f"market rows need finite positive {name} prices")
    if (highs < lows).any() or (highs < np.maximum(opens, closes)).any():
        raise ValueError("market rows have inconsistent high and low prices")
    if (lows > np.minimum(opens, closes)).any():
        raise ValueError("market rows have inconsistent high and low prices")
    if "volume" in work.columns:
        volumes = work["volume"].to_numpy().astype(float)
    else:
        volumes = np.full(n, np.nan)
    invalid_volume = np.isinf(volumes) | (volumes < 0)
    if invalid_volume.any():
        raise ValueError("market rows have infinite or negative volume")
    ts_values = work["ts"].to_numpy()

    # One group per distinct timestamp. ``np.unique`` sorts, and the frame
    # already is sorted, so ``starts`` are each timestamp's first index.
    unique_ts, starts = np.unique(ts_values, return_index=True)
    timestamps = [pd_ts.item() if hasattr(pd_ts, "item") else pd_ts for pd_ts in unique_ts]
    stops = np.append(starts[1:], n)

    snapshots: list[MarketSnapshot] = []
    for group, start, stop in zip(range(len(starts)), starts, stops, strict=True):
        ts = timestamps[group]
        bars: dict[str, Bar] = {}
        for j in range(start, stop):
            bars[symbols[j]] = Bar(
                ts=ts,
                open=opens[j],
                high=highs[j],
                low=lows[j],
                close=closes[j],
                volume=None if np.isnan(volumes[j]) else volumes[j],
            )
        snapshots.append(MarketSnapshot(ts=ts, bars=bars))
    return snapshots


def filter_snapshots(
    snapshots: list[MarketSnapshot], start: datetime | None, end: datetime | None
) -> list[MarketSnapshot]:
    out = snapshots
    if start is not None:
        out = [s for s in out if s.ts >= start]
    if end is not None:
        out = [s for s in out if s.ts <= end]
    return out
