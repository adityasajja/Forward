"""Read local market inputs for signal-context scoring."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from forward.core.historical_market_data_ports import DailyBarCachePort
from forward.core.learning_ports import (
    LearningArtifactsPort,
)
from forward.core.signal_context_ports import (
    SignalContextMarketDataPort,
)
from forward.data.history import DAILY_DIR


class LocalSignalContextMarketDataAdapter(SignalContextMarketDataPort):
    """Load signal-context market inputs through shared cache adapters.

    Args:
        data_root: Configured root that contains the daily market cache.
        exchange: Exchange segment used by the signal-context market scan.
        daily_bars: Shared adapter for loading cached parquet frames.
        learning_artifacts: Adapter that provides the sector map.
    """

    def __init__(
        self,
        data_root: Path,
        exchange: str,
        daily_bars: DailyBarCachePort,
        learning_artifacts: LearningArtifactsPort,
    ) -> None:
        self._daily_directory = data_root / DAILY_DIR / exchange.upper()
        self._daily_bars = daily_bars
        self._learning_artifacts = learning_artifacts

    def daily_frames(self) -> Mapping[str, Any]:
        """Return every daily frame available in the configured cache.

        Returns:
            Frames keyed by the source file stem.
        """
        filenames = sorted(path.stem for path in self._daily_directory.glob("*.parquet"))
        return self._daily_bars.load(self._daily_directory, filenames)

    def daily_frame(self, symbol: str) -> Any | None:
        """Return one symbol's daily frame, preferring its equity-series file.

        Args:
            symbol: Canonical trading symbol.

        Returns:
            The cached frame, or ``None`` when neither supported file is present.
        """
        filenames = (f"{symbol}-EQ", symbol)
        frames = self._daily_bars.load(self._daily_directory, filenames)
        return next((frames[name] for name in filenames if name in frames), None)

    def sector_table(self) -> Mapping[str, str]:
        """Return the sector mapping from the learning artifact adapter.

        Returns:
            Sector names keyed by canonical trading symbol.
        """
        return self._learning_artifacts.sector_table()
