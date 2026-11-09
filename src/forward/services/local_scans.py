"""Run scans against the local daily market-data cache."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

import pandas as pd

from forward.core.errors import MarketHistoryUnavailable
from forward.core.historical_market_data_ports import CachedMarketFramesPort
from forward.scanner import score_frame
from forward.scanner_custom import run_custom_scan


class LocalScanService:
    """Run and cache full-market and custom scans over cached bars."""

    def __init__(
        self,
        history: CachedMarketFramesPort,
        cache_seconds: float,
        default_symbols: list[str],
    ) -> None:
        """Create the local scan service.

        Args:
            history: Port that reads cached daily market frames.
            cache_seconds: How long full-market results stay in memory.
            default_symbols: Symbols used by an empty selected watchlist.
        """
        self._history = history
        self._cache_seconds = cache_seconds
        self._default_symbols = default_symbols
        self._scan_result: dict[str, Any] | None = None
        self._scan_exchange = ""
        self._scan_cached_at = 0.0

    def scan_all(self, exchange: str) -> dict[str, Any]:
        """Score every cached symbol for one exchange.

        Args:
            exchange: Exchange segment whose cached bars should be scanned.

        Returns:
            Scan date, universe size, breadth, and rows sorted by score.

        Raises:
            MarketHistoryUnavailable: If the exchange has no cached bars.
        """
        normalized_exchange = exchange.upper()
        now = time.monotonic()
        if (
            self._scan_result is not None
            and self._scan_exchange == normalized_exchange
            and now - self._scan_cached_at < self._cache_seconds
        ):
            return self._scan_result

        frames = self._history.frames(normalized_exchange)
        if not frames:
            raise MarketHistoryUnavailable(normalized_exchange)

        rows = []
        for symbol, frame in frames.items():
            try:
                rows.append(score_frame(symbol, frame))
            except Exception as error:
                raise ValueError(f"could not score {symbol}: {error}") from error

        scan = pd.DataFrame(rows).sort_values("score", ascending=False)
        result = {
            "as_of": self._frames_as_of(frames),
            "universe": len(frames),
            "scored": len(scan),
            "breadth_up": int((scan["trend"] == "UP").sum()),
            "rows": scan.to_dict(orient="records"),
        }
        self._scan_result = result
        self._scan_exchange = normalized_exchange
        self._scan_cached_at = now
        return result

    def scan_custom(
        self,
        *,
        exchange: str,
        conditions: list[dict[str, Any]],
        combine: str,
        symbols: list[str] | None = None,
        use_default_universe: bool = False,
    ) -> dict[str, Any]:
        """Run user-defined conditions over selected cached symbols.

        Args:
            exchange: Exchange segment whose cached bars should be scanned.
            conditions: Indicator rules accepted by the custom scan engine.
            combine: Rule combination mode accepted by the custom scan engine.
            symbols: Optional exact symbol list to restrict the scan.
            use_default_universe: Whether an empty list selects configured symbols.

        Returns:
            Scan date, input universe size, match count, and matching rows.

        Raises:
            MarketHistoryUnavailable: If no requested symbol has cached bars.
        """
        started_at = time.monotonic()
        selected_symbols = symbols
        if use_default_universe and not selected_symbols:
            selected_symbols = self._default_symbols
        frames = self._history.frames(exchange.upper(), symbols=selected_symbols)
        if not frames:
            raise MarketHistoryUnavailable(exchange.upper())

        rows = run_custom_scan(frames, conditions, combine=combine)
        return {
            "as_of": self._frames_as_of(frames),
            "universe_size": len(frames),
            "matched": len(rows),
            "elapsed_s": round(time.monotonic() - started_at, 2),
            "rows": rows,
        }

    def invalidate_full_market_scan(self) -> None:
        """Clear the cached full-market result after new bars arrive."""
        self._scan_result = None
        self._scan_exchange = ""
        self._scan_cached_at = 0.0

    @staticmethod
    def _frames_as_of(frames: Mapping[str, Any]) -> str:
        """Return the newest bar date after validating every selected frame.

        Raises:
            ValueError: If a selected symbol has no readable timestamp.
        """
        newest = None
        for symbol, frame in frames.items():
            try:
                timestamp = frame["ts"].iloc[-1]
            except Exception as error:
                raise ValueError(
                    f"cached bars for {symbol} have no readable latest timestamp: {error}"
                ) from error
            if newest is None or timestamp > newest:
                newest = timestamp
        if newest is None:
            raise ValueError("no cached bar timestamps are available")
        return newest.date().isoformat()
