"""Market-data adapter for the screener use case."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import polars as pl

from forward.core.errors import UnknownScreenerUniverse
from forward.core.screener_ports import ScreenerMarketDataPort
from forward.core.symbols import canonical_symbol
from forward.data import panel
from forward.data.history import DAILY_DIR
from forward.data.instrument_catalog import InstrumentMaster

logger = logging.getLogger("forward.screener.market")

@dataclass
class _FrameCache:
    """Hold the complete market frame set for one exchange."""

    frames: dict[str, pd.DataFrame]
    loaded_at: float

    def fresh(self, ttl_seconds: float) -> bool:
        """Return whether the cached frames are inside their time-to-live."""
        return time.monotonic() - self.loaded_at < ttl_seconds


_FRAMES: dict[str, _FrameCache] = {}
_FRAMES_LOCK = threading.Lock()


class ScreenerMarketDataAdapter(ScreenerMarketDataPort):
    """Read screener universes and daily bars from the local market cache."""

    def __init__(self, master: InstrumentMaster, cache_ttl_seconds: float) -> None:
        """Create the adapter.

        Args:
            master: Instrument index built from the local market cache.
            cache_ttl_seconds: Required cache lifetime for loaded frames.
        """
        self._master = master
        self._cache_ttl_seconds = cache_ttl_seconds

    def universes(self, exchange: str) -> list[dict[str, Any]]:
        """Return named universes and their available history counts.

        Args:
            exchange: Exchange segment to report.

        Returns:
            Universe names, labels, member counts, and loadable counts.
        """
        snapshot = self._master.snapshot()
        output = []
        for name in sorted(snapshot.universes):
            members = snapshot.universes[name]
            loadable = [
                symbol
                for symbol in members
                if (record := self._master.get(symbol)) is not None and record.has_daily
            ]
            output.append(
                {
                    "name": name,
                    "label": _universe_label(name),
                    "size": len(loadable),
                    "members": len(members),
                    "missing_history": len(members) - len(loadable),
                }
            )

        all_symbols = sorted(
            record.symbol
            for record in snapshot.records.values()
            if record.exchange.upper() == exchange.upper() and record.has_daily
        )
        if all_symbols:
            output.insert(
                0,
                {
                    "name": "all",
                    "label": "All cached NSE equities",
                    "size": len(all_symbols),
                    "members": len(all_symbols),
                    "missing_history": 0,
                },
            )
        return output

    def symbols_for(self, universe: str, exchange: str) -> list[str]:
        """Resolve a universe to symbols with daily history.

        Args:
            universe: Named universe or ``all``.
            exchange: Exchange segment to scan.

        Returns:
            Symbols that have cached daily bars.

        Raises:
            UnknownScreenerUniverse: If the requested universe is not known.
        """
        name = str(universe or "all").strip().lower()
        snapshot = self._master.snapshot()
        if name in ("all", ""):
            return sorted(
                record.symbol
                for record in snapshot.records.values()
                if record.exchange.upper() == exchange.upper() and record.has_daily
            )

        members = self._master.universe(name)
        if not members:
            raise UnknownScreenerUniverse(name, sorted(snapshot.universes))
        return [
            symbol
            for symbol in members
            if (record := self._master.get(symbol)) is not None and record.has_daily
        ]

    def frames(
        self,
        exchange: str,
        symbols: Sequence[str] | None = None,
        *,
        force: bool = False,
    ) -> dict[str, pd.DataFrame]:
        """Load daily frames keyed by canonical symbol.

        Args:
            exchange: Exchange segment to read.
            symbols: Optional exact symbols to read.
            force: Reload the complete exchange cache when true.

        Returns:
            Available daily market frames.
        """
        exchange = str(exchange or "NSEEQ").upper()
        if symbols is not None:
            return self._read_subset(exchange, symbols)

        with _FRAMES_LOCK:
            cached = _FRAMES.get(exchange)
            if cached is not None and cached.fresh(self._cache_ttl_seconds) and not force:
                return cached.frames

        started = time.monotonic()
        root = self._master.cache_root
        raw = dict(panel.frames(root / DAILY_DIR / exchange))
        frames = _canonical_frames(raw)
        logger.info(
            "screener: loaded %d frames for %s in %.1fs",
            len(frames),
            exchange,
            time.monotonic() - started,
        )
        with _FRAMES_LOCK:
            _FRAMES[exchange] = _FrameCache(frames=frames, loaded_at=time.monotonic())
        return frames

    def _read_subset(self, exchange: str, symbols: Sequence[str]) -> dict[str, pd.DataFrame]:
        """Read exact symbol files without replacing the complete cache."""
        wanted = [str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()]
        output_dir = self._master.cache_root / DAILY_DIR / exchange
        stems: list[str] = []
        for symbol in wanted:
            record = self._master.get(symbol)
            if record is not None and record.cache_file:
                stems.append(Path(record.cache_file).stem)

        def read_stem(stem: str) -> tuple[str, pd.DataFrame] | None:
            path = output_dir / f"{stem}.parquet"
            if not path.exists():
                return None
            try:
                return stem, pl.read_parquet(path).to_pandas()
            except Exception as exc:  # noqa: BLE001 - a corrupt file cannot be scanned
                logger.warning("screener: cannot read {}: {}", path, exc)
                return None

        frames: dict[str, pd.DataFrame] = {}
        if len(stems) <= 4:
            results = map(read_stem, stems)
            for result in results:
                if result is not None:
                    frames[result[0]] = result[1]
        else:
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=min(8, len(stems))) as pool:
                for result in pool.map(read_stem, stems):
                    if result is not None:
                        frames[result[0]] = result[1]
        return _canonical_frames(frames)


def clear_screener_frame_cache() -> None:
    """Drop cached screener frames after a market-history change."""
    with _FRAMES_LOCK:
        _FRAMES.clear()


def _canonical_frames(frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Key cache frames by bare symbol and prefer the listed equity series."""
    canonical: dict[str, pd.DataFrame] = {}
    equity_series: dict[str, bool] = {}
    for source_name, frame in frames.items():
        symbol = canonical_symbol(source_name)
        if not symbol:
            continue
        is_equity_series = source_name.endswith("-EQ")
        if symbol not in canonical or (is_equity_series and not equity_series[symbol]):
            canonical[symbol] = frame
            equity_series[symbol] = is_equity_series
    return canonical


def _universe_label(name: str) -> str:
    """Return the display label for a known universe."""
    labels = {
        "nifty50": "NIFTY 50",
        "next50": "NIFTY Next 50",
        "midcap150": "NIFTY Midcap 150",
        "smallcap250": "NIFTY Smallcap 250",
    }
    return labels.get(name, name.replace("_", " ").title())
