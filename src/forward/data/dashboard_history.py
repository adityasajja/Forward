"""Read daily closes from Forward's local history cache."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

from forward.core.market_models import DailyClose, DashboardHistorySample
from forward.data.history import load_cached


class CachedDashboardHistoryAdapter:
    """Read only the daily close data used by dashboard metrics."""

    def latest_closes(
        self, symbols: Sequence[str], exchange: str
    ) -> Mapping[str, DailyClose]:
        """Read each requested symbol's most recent cached close.

        Args:
            symbols: Canonical symbols to read.
            exchange: Exchange segment for the history files.

        Returns:
            Latest cached close and session date for each available symbol.
        """
        if not symbols:
            return {}
        wanted = [symbol.upper() for symbol in symbols]
        frames = load_cached(exchange=exchange, symbols=wanted)

        latest: dict[str, DailyClose] = {}
        for symbol, frame in frames.items():
            if frame.empty:
                continue
            session = self._last_session(frame)
            close = float(frame["close"].iloc[-1])
            if not math.isfinite(close) or close <= 0:
                raise ValueError(f"{symbol}: latest cached close must be finite and positive")
            latest[symbol.upper()] = DailyClose(session=session, close=close)
        return latest

    def breadth_history(
        self,
        exchange: str,
        sample_size: int,
        lookback_bars: int,
        minimum_bars: int,
    ) -> DashboardHistorySample:
        """Read a sorted sample of recent closes for one exchange.

        Args:
            exchange: Exchange segment for the history files.
            sample_size: Maximum number of symbols to return.
            lookback_bars: Maximum number of closes to return per symbol.
            minimum_bars: Minimum history needed for a symbol to be included.

        Returns:
            Recent closes and the full exchange universe size.
        """
        frames = load_cached(exchange.upper())
        if not frames:
            raise ValueError("history cache is empty — run `forward history sync`")

        sample: dict[str, tuple[DailyClose, ...]] = {}
        for symbol, frame in sorted(frames.items())[:sample_size]:
            if frame is None or len(frame) < minimum_bars:
                sample[symbol] = ()
                continue
            recent = frame.tail(lookback_bars)
            sessions = recent["ts"] if "ts" in recent.columns else recent.index
            closes = recent["close"].astype(float).to_numpy()
            if not np.isfinite(closes).all() or (closes <= 0).any():
                raise ValueError(f"{symbol}: breadth history has an invalid close")
            sample[symbol] = tuple(
                DailyClose(
                    session=self._calendar_date(session),
                    close=close,
                )
                for session, close in zip(
                    sessions,
                    closes,
                    strict=True,
                )
            )
        return DashboardHistorySample(universe_size=len(frames), symbols=sample)

    @classmethod
    def _last_session(cls, frame: Any) -> str | None:
        """Return the last market date in a cached frame."""
        if frame.empty:
            return None
        if "ts" in frame.columns:
            value = frame["ts"].iloc[-1]
        elif len(frame.index):
            value = frame.index[-1]
        else:
            raise ValueError("cached history has no timestamp")
        return cls._calendar_date(value)

    @staticmethod
    def _calendar_date(value: Any) -> str:
        """Convert a cache timestamp into an ISO calendar date."""
        timestamp = pd.Timestamp(value)
        if pd.isna(timestamp):
            raise ValueError("cached history has an invalid timestamp")
        return str(timestamp.date())
