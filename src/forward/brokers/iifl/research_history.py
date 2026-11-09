"""IIFL adapter that builds deep daily feeds for strategy research."""

from __future__ import annotations

from collections.abc import Sequence

import polars as pl

from forward.brokers.iifl.client import IiflClient
from forward.brokers.iifl.contracts import InstrumentMaster
from forward.brokers.iifl.daily_history import load_daily
from forward.core.backtest_ports import (
    BacktestFeedPort,
    ResearchHistoryPort,
)
from forward.core.enums import Timeframe
from forward.core.feeds import ListFeed
from forward.core.instrument_models import Instrument
from forward.data.base import pivot_to_snapshots


class IiflResearchHistoryAdapter(ResearchHistoryPort):
    """Read exact contracts from IIFL and assemble their daily bars."""

    def __init__(self, client: IiflClient) -> None:
        """Create the IIFL research-history adapter.

        Args:
            client: Authenticated IIFL client for the research run.
        """
        self._client = client

    def load_daily_feed(
        self, symbols: Sequence[str], exchange: str, lookback_days: int
    ) -> tuple[BacktestFeedPort, list[str]]:
        """Build a daily backtest feed from symbols with available history.

        Args:
            symbols: Exact trading symbols to request.
            exchange: Exchange segment that lists the instruments.
            lookback_days: Calendar days of daily bars to request.

        Returns:
            The feed and sorted symbols that contributed bars.

        Raises:
            ValueError: If no requested symbol has usable daily bars.
        """
        master = InstrumentMaster(self._client)
        master.load_cached([exchange])
        frames: list[pl.DataFrame] = []
        used: list[str] = []

        with self._client:
            for symbol in symbols:
                try:
                    instrument = master.find(symbol, exchange)
                except KeyError:
                    continue
                frame = load_daily(
                    self._client, instrument, lookback_days=lookback_days
                )
                if frame.empty or "ts" not in frame.columns:
                    continue
                frames.append(
                    pl.from_pandas(frame).with_columns(pl.lit(symbol).alias("symbol"))
                )
                used.append(symbol)

        if not frames:
            raise ValueError("no daily history returned for any requested symbol")

        combined = pl.concat(frames, how="diagonal_relaxed").sort(
            "ts", maintain_order=True
        )
        snapshots = pivot_to_snapshots(combined, Timeframe.DAY_1)
        instruments = {
            symbol: Instrument(symbol=symbol, exchange=exchange) for symbol in used
        }
        return ListFeed(snapshots, instruments), sorted(used)
