"""Build the catalog of strategies available for research."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from forward.services.validation_reports import ValidationReportService

_TUNABLE: dict[str, list[str]] = {
    "sma_crossover": ["fast", "slow"],
    "momentum_breakout": ["lookahead", "volume_multiple", "consecutive_breakout"],
    "signals_entry": [
        "trend_fast_sma",
        "trend_slow_sma",
        "pullback_rsi_low",
        "pullback_rsi_high",
    ],
    "opening_range_breakout": [],
    "cross_sectional_momentum": [],
    "paper_jegadeesh_titman": [],
    "paper_avellaneda_lee": [],
    "paper_volatility_breakout": [],
    "paper_multi_factor_composite": [],
    "paper_iima_nse_momentum": [],
    "paper_nism_52w_high": [],
    "paper_sehgal_low_vol": [],
}

_UNTESTED = {"state": "untested"}


class ResearchCatalogService:
    """Combine the built-in strategy registry and its validation results."""

    def __init__(
        self,
        strategies: Mapping[str, Any],
        warmup_bars: Mapping[str, int],
        validation_reports: ValidationReportService,
    ) -> None:
        """Create the catalog service.

        Args:
            strategies: Registered built-in strategy classes.
            warmup_bars: Minimum history needed before each strategy can trade.
            validation_reports: Reader for saved walk-forward results.
        """
        self._strategies = strategies
        self._warmup_bars = warmup_bars
        self._validation_reports = validation_reports

    def list_strategies(self) -> dict[str, Any]:
        """Return built-in strategies with their available validation evidence.

        Returns:
            Strategy names, tunable settings, warmup needs, and validation data.
        """
        payload = self._validation_reports.paper_validation()
        measured: dict[str, dict[str, Any]] = {}
        if payload.get("available"):
            for result in payload.get("results", []):
                measured[result.get("strategy", "")] = result

        rows = []
        for name in sorted(self._strategies):
            result = measured.get(name)
            rows.append(
                {
                    "name": name,
                    "tunable": _TUNABLE.get(name, []),
                    "warmup_bars": self._warmup_bars.get(name),
                    "validation": self._validation(result, payload),
                }
            )

        return {
            "strategies": rows,
            "validation_as_of": payload.get("generated_at") if measured else None,
            "control_sharpe": (payload.get("control", {}).get("sharpe_mean") if measured else None),
        }

    def strategy_class(self, name: str) -> Any | None:
        """Return one registered strategy class by its key.

        Args:
            name: Built-in strategy key.

        Returns:
            The strategy class, or ``None`` when the key is unknown.
        """
        return self._strategies.get(name)

    def strategy_names(self) -> list[str]:
        """Return all registered strategy keys in display order."""
        return sorted(self._strategies)

    def warmup_bars(self, name: str) -> int:
        """Return the history bars needed before a strategy can trade.

        Args:
            name: Built-in strategy key.

        Returns:
            Required history bars, or zero when no warmup is registered.
        """
        return self._warmup_bars.get(name, 0)

    @staticmethod
    def parameter_grid(
        strategy: str,
        *,
        fast: list[int],
        slow: list[int],
        search: bool,
    ) -> dict[str, list[Any]]:
        """Return the parameter grid used by one research run.

        Args:
            strategy: Built-in strategy key.
            fast: Fast SMA values requested by the editor.
            slow: Slow SMA values requested by the editor.
            search: Whether to use the entry-signal search grid.

        Returns:
            Parameter values the walk-forward runner will compare.
        """
        if strategy == "sma_crossover":
            return {"fast": fast, "slow": slow}
        if strategy == "signals_entry" and search:
            from forward.signals.models import SEARCH_GRID

            return {key: list(values) for key, values in SEARCH_GRID.items()}
        return {}

    @staticmethod
    def _validation(
        result: dict[str, Any] | None, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Return measured evidence or the explicit untested state."""
        if result is None:
            return _UNTESTED.copy()
        return {
            "state": "pass" if result.get("passed") else "fail",
            "oos_sharpe": result.get("oos_sharpe"),
            "oos_return_pct": result.get("oos_return_pct"),
            "benchmark_sharpe": result.get("benchmark_sharpe"),
            "deflated_sharpe": result.get("deflated_sharpe"),
            "measured_win_rate": result.get("measured_win_rate"),
            "measured_trades": result.get("measured_trades"),
            "expectancy_r": result.get("measured_expectancy_r"),
            "z_vs_control": result.get("sharpe_z_vs_control"),
            "as_of": payload.get("generated_at"),
        }
