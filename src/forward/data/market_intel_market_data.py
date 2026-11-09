"""Read the local market history used by the market-intelligence service."""

from __future__ import annotations

import csv
import hashlib
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pandas as pd

from forward.core.historical_market_data_ports import DailyBarCachePort
from forward.core.market_intel_ports import (
    MarketIntelMarketDataPort,
)
from forward.core.symbols import canonical_symbol
from forward.data.history import DAILY_DIR
from forward.market_intel.models import (
    BENCHMARK_KIND_UNAVAILABLE,
    PROVENANCE_NIFTY50_INDEX,
    BenchmarkProvenance,
)

logger = logging.getLogger("forward.market_intel")


class LocalMarketIntelDataAdapter(MarketIntelMarketDataPort):
    """Read universe files, daily bars, and benchmark history.

    Args:
        data_root: Configured root containing the local market data.
        exchange: Exchange segment containing the daily bar cache.
        daily_bars: Shared adapter for reading parquet bars.
    """

    def __init__(
        self, data_root: Path, exchange: str, daily_bars: DailyBarCachePort
    ) -> None:
        self._data_root = data_root
        self._universe_directory = data_root / "universe"
        self._daily_directory = data_root / DAILY_DIR / exchange.upper()
        self._daily_bars = daily_bars
        self._sector_map: dict[str, str] | None = None
        self._universe_symbols: list[str] | None = None

    def sector_map(self) -> dict[str, str]:
        """Return canonical symbol-to-industry names from universe CSV files.

        Returns:
            Industry names keyed by canonical symbol.
        """
        if self._sector_map is not None:
            return self._sector_map

        industries: dict[str, str] = {}
        if self._universe_directory.is_dir():
            for csv_path in sorted(self._universe_directory.glob("ind_*list.csv")):
                try:
                    with csv_path.open(
                        "r", encoding="utf-8", errors="replace"
                    ) as csv_file:
                        for row in csv.DictReader(csv_file):
                            symbol = canonical_symbol(row.get("Symbol") or "")
                            industry = (row.get("Industry") or "").strip()
                            if symbol and industry:
                                industries.setdefault(symbol, industry)
                except Exception as exc:
                    logger.warning("Error reading universe CSV %s: %s", csv_path, exc)

        self._sector_map = industries
        return industries

    def universe_symbols(self) -> list[str]:
        """Return the sorted symbols available to the market scan.

        Returns:
            Symbols named by the sector files.
        """
        if self._universe_symbols is not None:
            return self._universe_symbols

        self._universe_symbols = sorted(self.sector_map())
        return self._universe_symbols

    def load_frame(self, symbol: str) -> pd.DataFrame | None:
        """Return one cached daily frame using the supported symbol spellings.

        Args:
            symbol: Canonical or exchange-qualified symbol.

        Returns:
            A shallow frame copy, or None when no matching frame is cached.
        """
        clean_symbol = canonical_symbol(symbol)
        frames = self._daily_bars.load(self._daily_directory, (clean_symbol,))
        frame = frames.get(clean_symbol)
        return frame.copy(deep=False) if frame is not None and not frame.empty else None

    def benchmark_frame_and_provenance(
        self,
    ) -> tuple[pd.DataFrame | None, BenchmarkProvenance]:
        """Return the preferred benchmark frame and its source record.

        Returns:
            The official index or supported ETF proxy and its provenance.
        """
        from forward.data.indices import load_index

        frame = load_index(self._data_root)
        if frame is not None:
            return frame, PROVENANCE_NIFTY50_INDEX

        unavailable = BenchmarkProvenance(
            symbol="UNAVAILABLE",
            display_name="Unavailable Benchmark",
            kind=BENCHMARK_KIND_UNAVAILABLE,
            tracking_target="NIFTY 50",
            is_proxy=None,
            notes="No cached benchmark series found in local repository.",
        )
        return None, unavailable

    def vix_frame(self) -> pd.DataFrame | None:
        """Return the cached India VIX series when it exists.

        Returns:
            VIX daily bars, or None when the series has not been cached.
        """
        from forward.data.indices import INDIA_VIX, load_index

        return load_index(self._data_root, INDIA_VIX)

    def fingerprint(
        self, configuration: Any, source_files: Sequence[Path]
    ) -> str:
        """Hash the market files and model sources that affect a snapshot.

        Args:
            configuration: Regime thresholds used by the calculation.
            source_files: Service and model files that affect the calculation.

        Returns:
            SHA-256 fingerprint for the current inputs.
        """
        from forward.data import panel
        from forward.data.indices import INDIA_VIX, NIFTY_50, index_path

        digest = hashlib.sha256()
        digest.update(
            repr(sorted(panel._scan(self._daily_directory).items())).encode()
        )
        files = [
            index_path(self._data_root, name) for name in (NIFTY_50, INDIA_VIX)
        ]
        if self._universe_directory.is_dir():
            files.extend(sorted(self._universe_directory.glob("ind_*list.csv")))
        files.extend(source_files)
        for path in files:
            try:
                details = path.stat()
                digest.update(
                    f"{path.name}:{details.st_mtime_ns}:{details.st_size}".encode()
                )
            except OSError:
                digest.update(f"{path.name}:missing".encode())
        digest.update(repr(configuration).encode())
        return digest.hexdigest()
