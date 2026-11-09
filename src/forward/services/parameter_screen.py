"""Run bounded parameter screens with cached market history."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from forward.core.historical_market_data_ports import CachedMarketFramesPort


class ScreeningInProgressError(RuntimeError):
    """Raised when another parameter screen already owns the worker."""


@dataclass(frozen=True)
class ParameterScreenConfig:
    """Required parameters for a bounded candidate screen."""

    exchange: str
    minimum_bars: int
    fast_periods: tuple[int, ...]
    slow_periods: tuple[int, ...]
    chunk_size: int
    candidate_limit: int
    screen_cost_bps: float


class ParameterScreenService:
    """Run the SMA parameter screen from the configured daily history port."""

    def __init__(
        self, market_history: CachedMarketFramesPort, config: ParameterScreenConfig
    ) -> None:
        """Create the screen service.

        Args:
            market_history: Adapter that reads cached daily bars by exchange.
            config: Required parameter ranges and screen-report settings.
        """
        self._market_history = market_history
        self._config = config
        self._lock = threading.Lock()

    def device_info(self) -> dict[str, Any]:
        """Return the compute devices available to the screen.

        Returns:
            PyTorch device details for this machine.
        """
        from forward.compute.device import device_info

        return device_info()

    def market_matrix(
        self,
        days: int,
        *,
        exchange: str,
        min_bars: int,
        max_symbols: int | None,
    ) -> tuple[Any, list[str], Any]:
        """Build the dense close matrix used by command-line screens.

        Args:
            days: Maximum number of recent market bars to include.
            exchange: Exchange whose cached bars should be read.
            min_bars: Minimum history required for each symbol.
            max_symbols: Maximum number of symbols to keep.

        Returns:
            Close matrix, symbol names, and bar dates.
        """
        from forward.compute.sweep import close_matrix

        frames = self._market_history.frames(exchange)
        return close_matrix(
            frames, days=days, min_bars=min_bars, max_symbols=max_symbols
        )

    def screen(self, days: int, max_symbols: int) -> dict[str, Any]:
        """Run one bounded candidate screen and return its public result.

        Args:
            days: Maximum number of recent market bars to include.
            max_symbols: Maximum number of symbols to screen.

        Returns:
            Screening metrics and the best candidate parameter pairs.

        Raises:
            ScreeningInProgressError: If another screen is already running.
            ValueError: If the cache does not hold enough history.
        """
        if not self._lock.acquire(blocking=False):
            raise ScreeningInProgressError(
                "A candidate screen is already running. Try again when it finishes."
            )

        try:
            from forward.compute.holdout import training_window
            from forward.compute.sweep import sma_cross_sweep

            close, names, dates = self.market_matrix(
                days,
                exchange=self._config.exchange,
                min_bars=self._config.minimum_bars,
                max_symbols=max_symbols,
            )
            if not names or close.shape[0] < self._config.minimum_bars:
                raise ValueError(
                    f"At least {self._config.minimum_bars} cached daily bars are required."
                )

            training, eligible, split = training_window(close, dates)
            names = [name for name, keep in zip(names, eligible, strict=False) if keep]
            if not names:
                raise ValueError(
                    f"No stocks have {self._config.minimum_bars} training bars."
                )

            result = sma_cross_sweep(
                training,
                list(self._config.fast_periods),
                list(self._config.slow_periods),
                chunk=self._config.chunk_size,
            )
            device = self.device_info()
            return {
                "device": result.device,
                "hardware": device["name"] if device["cuda_available"] else "CPU",
                "seconds": result.seconds,
                "combinations": result.combos,
                "symbols": result.symbols,
                "bars": result.bars,
                "stock_symbols": names,
                "start": split["train_start"],
                "end": split["train_end"],
                "split": split,
                "screen_cost_bps": self._config.screen_cost_bps,
                "candidates": result.top(self._config.candidate_limit),
                "screening_only": True,
            }
        finally:
            self._lock.release()
