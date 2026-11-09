"""IIFL adapter for downloading daily history into the local cache."""

from __future__ import annotations

import os
import re
import tempfile
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from functools import lru_cache
from hashlib import sha256
from pathlib import Path

import pandas as pd
import polars as pl
from loguru import logger

from forward.brokers.iifl.client import INTERVALS, IiflClient, parse_history_date
from forward.brokers.iifl.contracts import InstrumentMaster
from forward.data.history import CACHE_ROOT

COLUMNS = ["ts", "open", "high", "low", "close", "volume"]


class IiflHistorySyncAdapter:
    """Download contract history using a per-thread authenticated client."""

    def __init__(
        self,
        client_factory: Callable[[], IiflClient],
        cache_root: Path = CACHE_ROOT,
    ) -> None:
        """Create the history adapter.

        Args:
            client_factory: Return an authenticated client for the current thread.
            cache_root: Directory where downloaded exchange files are stored.
        """
        self._client_factory = client_factory
        self._cache_root = cache_root

    def sync_all(
        self,
        exchange: str,
        interval: str,
        from_date: str | None,
        to_date: str | None,
        *,
        workers: int,
        pause: float = 0.2,
        start: int = 0,
        end: int | None = None,
    ) -> Counter[str]:
        """Download a slice of the cached contract universe.

        Args:
            exchange: Exchange segment whose contracts are downloaded.
            interval: IIFL candle interval.
            from_date: First date in IIFL format, or ``None`` for one year ago.
            to_date: Last date in IIFL format, or ``None`` for today.
            workers: Maximum concurrent history requests.
            pause: Delay between requests on each worker thread.
            start: First contract index to download.
            end: Contract index after the final download, or ``None`` for all.

        Returns:
            Counts by result: ``ok``, ``skip``, ``empty``, or ``fail``.
        """
        today = date.today()
        end_date = to_date or today.strftime("%d-%b-%Y")
        start_date = from_date or (today - timedelta(days=365)).strftime("%d-%b-%Y")
        start_key = parse_history_date(start_date).date().isoformat()
        end_key = parse_history_date(end_date).date().isoformat()
        if end_key < start_key:
            raise ValueError("history cache end date must not be before its start date")
        contracts = self._universe(exchange)[start:end]
        output_dir = self._cache_root / exchange.upper()
        output_dir.mkdir(parents=True, exist_ok=True)
        interval_name = INTERVALS.get(interval, interval)
        interval_key = _interval_cache_key(interval_name)
        partition_dir = (
            output_dir / "_ranges" / interval_key / f"{start_key}__{end_key}"
        )
        partition_dir.mkdir(parents=True, exist_ok=True)
        is_daily = interval_name == "1 day"
        logger.info(
            "syncing {} {} bars {} to {} ({} symbols, {} workers)",
            exchange,
            interval,
            start_date,
            end_date,
            len(contracts),
            workers,
        )

        counts: Counter[str] = Counter()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    self._sync_one,
                    exchange.upper(),
                    row.symbol,
                    row.conid,
                    interval,
                    start_date,
                    end_date,
                    partition_dir,
                    output_dir,
                    is_daily,
                    pause,
                ): row.symbol
                for row in contracts.itertuples(index=False)
            }
            for index, future in enumerate(as_completed(futures), 1):
                counts[future.result()] += 1
                if index % 250 == 0:
                    logger.info("{}/{} ... {}", index, len(futures), dict(counts))
        logger.info("done: {}", dict(counts))
        return counts

    @staticmethod
    @lru_cache(maxsize=4)
    def _universe(exchange: str) -> pd.DataFrame:
        """Load exact contracts from the local instrument master.

        Args:
            exchange: Exchange segment to read.

        Returns:
            Tradable symbols and their IIFL instrument IDs.
        """
        master = InstrumentMaster()
        frame = master.load_cached([exchange.upper()])
        return frame[
            (frame["exchange"].str.upper() == exchange.upper())
            & frame["symbol"].str.endswith("-EQ")
        ][["symbol", "conid"]].drop_duplicates().sort_values("symbol")

    def _sync_one(
        self,
        exchange: str,
        symbol: str,
        conid: str,
        interval: str,
        from_date: str,
        to_date: str,
        partition_dir: Path,
        output_dir: Path,
        is_daily: bool,
        pause: float,
    ) -> str:
        """Download one contract and write its ordered candles.

        Args:
            exchange: Exchange segment.
            symbol: Exact trading symbol.
            conid: IIFL instrument ID.
            interval: IIFL candle interval.
            from_date: First requested date.
            to_date: Last requested date.
            partition_dir: Cache directory for this interval and date range.
            output_dir: Exchange cache directory.
            is_daily: Whether to refresh the daily reader's materialized cache.
            pause: Delay before the worker starts another request.

        Returns:
            The result label for this contract.
        """
        path = partition_dir / f"{symbol}.parquet"
        if path.exists():
            try:
                if is_daily:
                    _write_daily_view(output_dir, symbol)
                return "skip"
            except Exception as error:
                logger.warning("{}: {}", symbol, str(error)[:120])
                return "fail"

        try:
            payload = self._client_factory().historical_data(
                exchange, str(conid), interval, from_date, to_date
            )
            candles = payload["result"][0]["candles"]
            bars = pl.DataFrame(candles, schema=COLUMNS, orient="row")
            if bars.is_empty():
                return "empty"
            timestamp = pl.col("ts")
            if bars.schema["ts"] == pl.Utf8:
                bars = bars.with_columns(timestamp.str.to_datetime(strict=False))
            else:
                bars = bars.with_columns(timestamp.cast(pl.Datetime))
            _write_parquet_atomically(bars.sort("ts"), path)
            if is_daily:
                _write_daily_view(output_dir, symbol)
            result = "ok"
        except Exception as error:  # One failed symbol should not stop the full sync.
            logger.warning("{}: {}", symbol, str(error)[:120])
            result = "fail"
        time.sleep(pause)
        return result


def _interval_cache_key(interval: str) -> str:
    """Return a stable, path-safe identity for one broker interval.

    Args:
        interval: Canonical interval sent to IIFL.

    Returns:
        A path component that cannot collide when distinct interval strings
        sanitize to the same readable name.
    """
    canonical = interval.casefold()
    digest = sha256(canonical.encode("utf-8")).hexdigest()
    readable = re.sub(r"[^a-z0-9]+", "_", canonical).strip("_")[:32]
    return f"{readable or 'interval'}-{digest}"


def _write_daily_view(exchange_dir: Path, symbol: str) -> None:
    """Refresh the flat daily cache from all cached daily range partitions.

    Args:
        exchange_dir: Exchange cache directory that contains ``_ranges``.
        symbol: Exact trading symbol to materialize.
    """
    daily_interval = _interval_cache_key(INTERVALS["1d"])
    paths = sorted(
        (exchange_dir / "_ranges" / daily_interval).glob(f"*/{symbol}.parquet")
    )
    frames = [pl.read_parquet(path) for path in paths]
    if not frames:
        return
    merged = pl.concat(frames, how="vertical").unique(maintain_order=True)
    conflicts = merged.group_by("ts").len().filter(pl.col("len") > 1)
    if conflicts.height:
        conflict_timestamp = conflicts.get_column("ts")[0]
        raise ValueError(
            f"cache conflict for {symbol}: different daily candles share timestamp "
            f"{conflict_timestamp}"
        )
    target = exchange_dir / f"{symbol}.parquet"
    _write_parquet_atomically(merged.sort("ts"), target)


def _write_parquet_atomically(frame: pl.DataFrame, path: Path) -> None:
    """Write a Parquet file without exposing a partial target file.

    Args:
        frame: Complete data to write.
        path: Final Parquet path.
    """
    with tempfile.NamedTemporaryFile(
        dir=path.parent,
        prefix=f".{path.stem}.",
        suffix=path.suffix,
        delete=False,
    ) as temporary_file:
        temporary_path = Path(temporary_file.name)
    try:
        frame.write_parquet(temporary_path)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
