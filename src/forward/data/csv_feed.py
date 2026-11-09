"""CSV / Parquet backed feed.

Expects either:
  * one file per symbol named ``<SYMBOL>.csv`` inside ``directory``, or
  * a single long-format file passed via ``path``.

Required columns: ts, open, high, low, close. Optional: volume, vwap,
open_interest.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import polars as pl

from forward.core.enums import Timeframe
from forward.core.instrument_models import Instrument
from forward.core.market_models import MarketSnapshot
from forward.data.base import DataFeed, filter_snapshots, pivot_to_snapshots

REQUIRED = {"ts", "symbol", "open", "high", "low", "close"}


class CsvFeed(DataFeed):
    def __init__(
        self,
        *,
        directory: str | Path | None = None,
        path: str | Path | None = None,
        timeframe: Timeframe = Timeframe.MIN_1,
        instruments: Mapping[str, Instrument],
    ) -> None:
        """Load market bars from exactly one CSV source.

        Args:
            directory: Folder with one CSV file per symbol.
            path: One long-format CSV file.
            timeframe: Bar interval stored in the source.
            instruments: Explicit instrument definitions keyed by symbol.

        Raises:
            ValueError: If both source locations are set or neither is set.
            FileNotFoundError: If a source folder has no CSV files.
        """
        if (directory is None) == (path is None):
            raise ValueError("provide exactly one of `directory` or `path`")
        self.timeframe = timeframe
        self._frame = self._read(directory, path)
        normalized = {symbol.strip().upper(): instrument for symbol, instrument in instruments.items()}
        if len(normalized) != len(instruments):
            raise ValueError("instrument keys must be unique after uppercasing")
        symbols = set(self._frame["symbol"].unique().to_list())
        missing_symbols = sorted(symbols - set(normalized))
        if missing_symbols:
            raise ValueError(f"instrument metadata is missing for: {missing_symbols}")
        mismatched_symbols = sorted(
            symbol
            for symbol, instrument in normalized.items()
            if instrument.symbol.strip().upper() != symbol
        )
        if mismatched_symbols:
            raise ValueError(
                f"instrument symbol does not match its key: {mismatched_symbols}"
            )
        self._instruments = dict(normalized)

    # ------------------------------------------------------------------
    def _read(self, directory, path) -> pl.DataFrame:
        if directory:
            frames = []
            for file in sorted(Path(directory).glob("*.csv")):
                df = pl.read_csv(file).with_columns(pl.lit(file.stem.upper()).alias("symbol"))
                frames.append(df)
            if not frames:
                raise FileNotFoundError(f"no CSV files found in {directory}")
            out = pl.concat(frames, how="diagonal_relaxed")
        else:
            out = pl.read_csv(Path(path))
        return _normalize_frame(out, str(directory or path))

    # ------------------------------------------------------------------
    @property
    def instruments(self) -> dict[str, Instrument]:
        return self._instruments

    def load(self, start=None, end=None) -> list[MarketSnapshot]:
        snaps = pivot_to_snapshots(self._frame, self.timeframe)
        return filter_snapshots(snaps, start, end)

    @property
    def frame(self) -> pl.DataFrame:
        return self._frame


def _parse_ts(df: pl.DataFrame) -> pl.DataFrame:
    """Parse the ts column (string or already-temporal) to UTC datetime."""
    if df.schema["ts"] == pl.Utf8:
        return df.with_columns(pl.col("ts").str.to_datetime(time_zone="UTC", strict=False))
    ts = df["ts"]
    expr = pl.col("ts").dt.replace_time_zone("UTC") if ts.dtype.time_zone is None else pl.col("ts").dt.convert_time_zone("UTC")
    return df.with_columns(expr)


class ParquetFeed(CsvFeed):
    """Same contract as CsvFeed but backed by Parquet (much faster for years
    of minute data)."""

    def _read(self, directory, path) -> pl.DataFrame:  # type: ignore[override]
        src = Path(path) if path else Path(directory)
        if src.is_dir():
            frames = []
            for file in sorted(src.glob("*.parquet")):
                df = pl.read_parquet(file).with_columns(pl.lit(file.stem.upper()).alias("symbol"))
                frames.append(df)
            if not frames:
                raise FileNotFoundError(f"no Parquet files found in {src}")
            out = pl.concat(frames, how="diagonal_relaxed")
        else:
            out = pl.read_parquet(src)
            if "symbol" not in {column.strip().lower() for column in out.columns}:
                out = out.with_columns(pl.lit(src.stem.upper()).alias("symbol"))
        return _normalize_frame(out, str(src))


def _normalize_frame(frame: pl.DataFrame, source: str) -> pl.DataFrame:
    """Normalize market columns and reject incomplete OHLC rows.

    Args:
        frame: CSV or Parquet rows to validate.
        source: Input path used in error messages.

    Returns:
        A sorted market frame with canonical column names.

    Raises:
        ValueError: If a required field or OHLC value is invalid.
    """
    out = frame.rename({column: column.strip().lower() for column in frame.columns})
    missing = REQUIRED - set(out.columns)
    if missing:
        raise ValueError(f"{source}: missing columns: {sorted(missing)}")
    out = _parse_ts(out)
    if out["ts"].null_count() > 0 or out["symbol"].null_count() > 0:
        raise ValueError(f"{source}: every market row needs a timestamp and symbol")
    out = out.with_columns(pl.col("symbol").cast(pl.Utf8).str.strip_chars().str.to_uppercase())
    if out.filter(pl.col("symbol") == "").height:
        raise ValueError(f"{source}: every market row needs a non-empty symbol")

    for column in ("open", "high", "low", "close", "volume", "vwap", "open_interest"):
        if column in out.columns:
            out = out.with_columns(pl.col(column).cast(pl.Float64, strict=True))

    for column in ("open", "high", "low", "close"):
        values = out[column]
        if values.null_count() or not values.is_finite().all() or not (values > 0).all():
            raise ValueError(f"{source}: OHLC column {column!r} must be finite and positive")
    inconsistent = out.filter(
        (pl.col("high") < pl.max_horizontal("open", "close"))
        | (pl.col("low") > pl.min_horizontal("open", "close"))
        | (pl.col("high") < pl.col("low"))
    )
    if inconsistent.height:
        raise ValueError(f"{source}: OHLC rows have inconsistent high and low prices")

    if "volume" in out.columns:
        out = out.with_columns(pl.col("volume").fill_nan(None))
        volume = out["volume"].drop_nulls()
        if not volume.is_finite().all() or (volume < 0).any():
            raise ValueError(f"{source}: volume must be finite and non-negative when present")
    return out.sort(["ts", "symbol"])
