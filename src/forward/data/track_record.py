"""Load price history and provenance for paper track records."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from loguru import logger

from forward.core.historical_market_data_ports import CachedInstrumentHistoryPort
from forward.core.market_tracking_ports import TrackRecordMarketDataPort
from forward.data.indices import NIFTY_50, index_path


class CachedTrackRecordMarketDataAdapter(TrackRecordMarketDataPort):
    """Read track-record prices from the configured local market cache."""

    def __init__(
        self, instruments: CachedInstrumentHistoryPort, data_root: Path
    ) -> None:
        """Create the adapter with the instrument and cache dependencies.

        Args:
            instruments: Port that resolves instruments and reads their daily bars.
            data_root: Configured root that contains the benchmark history.
        """
        self._instruments = instruments
        self._data_root = Path(data_root)

    def stock_closes(self, symbol: str, exchange: str) -> dict[date, float] | None:
        """Read valid daily closes for one listed instrument.

        Args:
            symbol: Trading symbol held by a paper deployment.
            exchange: Exchange segment for the instrument.

        Returns:
            Closes keyed by market date, or ``None`` when no history is available.
        """
        try:
            instrument = self._instruments.get(symbol)
            if (
                instrument is None
                or instrument.exchange.upper() != exchange.upper()
                or not instrument.cache_file
            ):
                return None
            frame = self._instruments.daily_bars(instrument.cache_file)
        except Exception as exc:  # noqa: BLE001 - missing history stays unpriced
            logger.warning("track record: could not read {}: {}", symbol, exc)
            return None
        try:
            if (
                frame is None
                or frame.empty
                or "close" not in frame.columns
                or "ts" not in frame.columns
            ):
                return None
            valid = frame[frame["close"].notna() & (frame["close"] > 0)]
            days = pd.to_datetime(valid["ts"]).dt.date
            return dict(zip(days, valid["close"].astype(float), strict=True))
        except Exception as exc:  # noqa: BLE001 - invalid cache rows are unavailable
            logger.warning("track record: could not read {}: {}", symbol, exc)
            return None

    def benchmark(self) -> tuple[dict[date, float] | None, dict[str, Any]]:
        """Read NIFTY 50 daily closes and their source details.

        Returns:
            Daily closes and benchmark provenance, or empty data when unavailable.
        """
        path = index_path(self._data_root, NIFTY_50)
        closes = self._series(path) if path.exists() else None
        if not closes:
            return None, {"name": NIFTY_50, "source": None, "as_of": None}
        try:
            source = path.with_suffix(".source").read_text(encoding="utf8").strip() or None
        except OSError:
            source = None
        return closes, {
            "name": NIFTY_50,
            "source": source,
            "as_of": max(closes).isoformat(),
        }

    def is_demo(self) -> bool:
        """Return whether the configured market data is marked as demo data."""
        return (self._data_root / ".demo").exists()

    @staticmethod
    def _series(path: Path) -> dict[date, float] | None:
        """Read daily closes from a parquet file.

        Args:
            path: Benchmark parquet file.

        Returns:
            Closes keyed by market date, or ``None`` when the file is unusable.
        """
        try:
            frame = pd.read_parquet(path)
        except Exception as exc:  # noqa: BLE001 - unreadable prices are unavailable
            logger.warning("track record: could not read {}: {}", path, exc)
            return None
        if frame.empty or "close" not in frame.columns or "ts" not in frame.columns:
            return None
        frame = frame[frame["close"].notna() & (frame["close"] > 0)]
        days = pd.to_datetime(frame["ts"]).dt.date
        return dict(zip(days, frame["close"].astype(float), strict=True))
