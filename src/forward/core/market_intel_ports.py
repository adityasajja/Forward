"""Ports for the related application workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol


class MarketIntelDailyBarsPort(Protocol):
    """Read daily bars needed for market-intelligence views."""

    def load_frame(self, symbol: str) -> Any | None:
        """Return one symbol's cached daily bars.



        Args:

            symbol: Canonical or exchange-qualified symbol.



        Returns:

            Daily bars, or None when no cache file exists.

        """


class MarketIntelMarketDataPort(MarketIntelDailyBarsPort, Protocol):
    """Read local market history used by market-intelligence calculations."""

    def sector_map(self) -> Mapping[str, str]:
        """Return canonical symbols mapped to their industries."""

    def universe_symbols(self) -> list[str]:
        """Return symbols in the configured market-intelligence universe."""

    def benchmark_frame_and_provenance(self) -> tuple[Any | None, Any]:
        """Return the preferred benchmark and its provenance record."""

    def vix_frame(self) -> Any | None:
        """Return the cached India VIX series when available."""

    def fingerprint(self, configuration: Any, source_files: Sequence[Path]) -> str:
        """Return an identity for the current local history and model inputs.



        Args:

            configuration: Regime configuration used by the calculation.

            source_files: Calculation and model files that affect its result.



        Returns:

            Stable fingerprint for the current inputs.

        """


class MarketIntelIndexRefreshPort(Protocol):
    """Refresh the cached index history when a refresh is due."""

    def refresh_if_due(self) -> None:
        """Refresh NIFTY 50 and India VIX history when required."""


class MarketIntelSummaryPort(Protocol):
    """Read a complete market-intelligence calculation."""

    def compute_all(self, *, force_refresh: bool = False) -> tuple[Any, list[Any], dict[str, Any]]:
        """Return the market summary, sector rankings, and stock contexts.



        Args:

            force_refresh: Rebuild the snapshot when its short-lived cache is fresh.



        Returns:

            The market summary, ordered sector metrics, and stock contexts by symbol.

        """


class MarketIntelSnapshotCachePort(Protocol):
    """Load and store a market-intelligence snapshot by input fingerprint."""

    def load(self, fingerprint: str) -> Any | None:
        """Return the snapshot for a fingerprint when one is stored.



        Args:

            fingerprint: Identity of the data and model configuration.



        Returns:

            The stored snapshot, or ``None`` when no matching snapshot exists.

        """

    def save(self, fingerprint: str, snapshot: Any) -> None:
        """Store a snapshot for its input fingerprint.



        Args:

            fingerprint: Identity of the data and model configuration.

            snapshot: Snapshot returned by the market-intelligence calculation.

        """
