"""Build chart candles from the live tick buffer with DuckDB."""

from __future__ import annotations

import threading
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import duckdb

# Keep one DuckDB connection for each worker thread.
_THREAD_LOCAL = threading.local()


class DuckEngine:
    """Aggregate live ticks using one DuckDB connection per thread."""

    def __init__(self) -> None:
        self._con = duckdb.connect()

    def resample_candles(
        self,
        _symbol: str,
        ticks: Sequence[dict[str, Any]],
        interval_seconds: int,
    ) -> list[dict[str, Any]]:
        """Convert supplied ticks into OHLCV candles.

        Args:
            _symbol: Instrument symbol required by the aggregation port.
            ticks: Ordered observations from the live ring buffer.
            interval_seconds: Width of each candle in seconds.

        Returns:
            Candles in ascending time order. An empty tick list returns no candles.
        """
        if not ticks:
            return []
        import pyarrow as pa

        table = pa.table(
            {
                "epoch": pa.array([tick["epoch"] for tick in ticks], type=pa.float64()),
                "ts": pa.array(
                    [datetime.fromisoformat(tick["ts"]) for tick in ticks],
                    type=pa.timestamp("us"),
                ),
                "ltp": pa.array([tick["ltp"] for tick in ticks], type=pa.float64()),
                "qty": pa.array([tick["qty"] for tick in ticks], type=pa.int32()),
            }
        )

        query = f"""
            WITH raw AS (
                SELECT
                    epoch,
                    ts,
                    ltp,
                    qty,
                    CAST(FLOOR(epoch / {interval_seconds}) * {interval_seconds} AS BIGINT) AS bucket
                FROM tick_table
            )
            SELECT
                bucket,
                FIRST(ltp ORDER BY epoch, ts) AS open,
                MAX(ltp) AS high,
                MIN(ltp) AS low,
                LAST(ltp ORDER BY epoch, ts) AS close,
                COALESCE(SUM(qty), 0) AS volume,
                LAST(ts ORDER BY epoch, ts) AS ts
            FROM raw
            GROUP BY bucket
            ORDER BY bucket ASC
        """
        self._con.register("tick_table", table)
        try:
            result = self._con.execute(query).fetchall()
        finally:
            self._con.unregister("tick_table")

        return [
            {
                "bucket": row[0],
                "open": row[1],
                "high": row[2],
                "low": row[3],
                "close": row[4],
                "volume": row[5],
                "ts": row[6].isoformat() if row[6] else None,
            }
            for row in result
        ]


def _get_thread_local_engine() -> DuckEngine:
    """Get or create a thread-local DuckDB engine instance."""
    if not hasattr(_THREAD_LOCAL, "engine"):
        _THREAD_LOCAL.engine = DuckEngine()
    return _THREAD_LOCAL.engine


def get_duck_engine() -> DuckEngine:
    """Get a DuckDB engine instance (thread-local for thread safety).

    Each thread gets its own DuckDB connection, which is required because
    DuckDB connections are not thread-safe for concurrent access.
    """
    return _get_thread_local_engine()
