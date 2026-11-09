"""Load completed minute candles and aggregate them within each NSE session."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pandas as pd

from forward.core.enums import Timeframe


def load_intraday(
    root: Path,
    symbols: Sequence[str],
    exchange: str,
    timeframe: str,
    end: str | None = None,
) -> dict[str, pd.DataFrame]:
    """Load completed intraday bars from the local minute cache.

    Args:
        root: Root directory that contains the market-data caches.
        symbols: Exact symbols requested by the backtest.
        exchange: Exchange segment for the market data.
        timeframe: Target candle size.
        end: Optional inclusive final market date.

    Returns:
        Intraday frames keyed by symbol, each indexed by timestamp.

    Raises:
        ValueError: If the exchange, timeframe, or cached candle data is invalid.
    """
    if exchange.upper() != "NSEEQ":
        raise ValueError("Intraday history currently supports NSE cash equities only")
    duration_seconds = Timeframe(timeframe).seconds
    if duration_seconds is None or duration_seconds < 60:
        raise ValueError(f"Intraday candles need a time-based timeframe: {timeframe}")
    minutes = duration_seconds // 60
    frames = {}
    for symbol in symbols:
        path = root / "iifl_1m" / f"{symbol}.parquet"
        if not path.exists():
            continue
        frame = pd.read_parquet(path)
        missing = sorted(
            {"ts", "open", "high", "low", "close", "volume"} - set(frame.columns)
        )
        if missing:
            raise ValueError(
                f"{symbol}: missing candle fields: {', '.join(missing)}; "
                "repair the minute history before testing"
            )
        stamp = pd.DatetimeIndex(pd.to_datetime(frame["ts"]))
        if stamp.tz is not None:
            stamp = stamp.tz_convert("Asia/Kolkata").tz_localize(None)
        frame = frame.drop(columns=["ts"]).set_axis(stamp).sort_index()
        if end:
            frame = frame.loc[frame.index < pd.Timestamp(end) + pd.Timedelta(days=1)]
        # Off-grid updates are live partial candles, not completed minute bars.
        frame = frame.loc[(frame.index.second == 0) & (frame.index.microsecond == 0)]
        frame = frame.reset_index(names="ts").drop_duplicates().set_index("ts")
        if frame.index.has_duplicates:
            raise ValueError(
                f"{symbol}: duplicate minute timestamps; "
                "repair the history before testing"
            )
        frame = frame.between_time("09:15", "15:29")
        if minutes > 1:
            pieces = []
            for _, day in frame.groupby(frame.index.date):
                grouped = day.resample(
                    f"{minutes}min",
                    origin="start_day",
                    offset="9h15min",
                    closed="left",
                    label="left",
                )
                bars = grouped.agg(
                    {
                        "open": "first",
                        "high": "max",
                        "low": "min",
                        "close": "last",
                        "volume": "sum",
                    }
                )
                # A partial bucket cannot masquerade as a complete candle.
                bars = bars.loc[grouped["close"].count() == minutes]
                pieces.append(bars)
            frame = pd.concat(pieces) if pieces else frame.iloc[:0]
        if not frame.empty:
            frame.index.name = "ts"
            frames[symbol] = frame
    return frames
