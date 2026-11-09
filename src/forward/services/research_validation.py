"""Run walk-forward validation on one selected research feed."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from forward.backtest.engine import BacktestConfig
from forward.config.costs import slippage_model
from forward.core.backtest_ports import BacktestFeedPort
from forward.execution.risk import RiskLimits
from forward.research.validate import (
    ValidationConfig,
    WalkForwardConfig,
    buy_and_hold_equity,
    walk_forward,
)
from forward.services.research_catalog import ResearchCatalogService


@dataclass(frozen=True)
class ResearchRunConfig:
    """Settings that control one walk-forward validation run."""

    strategy: str
    source: str
    cash: float
    train: int | None
    test: int | None
    step: int | None
    warmup: int | None
    fast: list[int]
    slow: list[int]
    search: bool
    min_trades: int
    min_folds: int
    confidence: float
    slippage_bps: float


class ResearchValidationError(ValueError):
    """A research run cannot form a valid train and test window."""


class ResearchValidationService:
    """Size walk-forward windows, run the strategy, and build its report."""

    def __init__(self, catalog: ResearchCatalogService) -> None:
        """Create the validation service.

        Args:
            catalog: Registry and metadata for built-in research strategies.
        """
        self._catalog = catalog

    def run(
        self,
        config: ResearchRunConfig,
        strategy_class: type,
        feed: BacktestFeedPort,
        symbols: list[str],
    ) -> dict[str, Any]:
        """Run walk-forward validation and build the API report data.

        Args:
            config: Strategy, window, and cost settings for the run.
            strategy_class: Registered strategy implementation to evaluate.
            feed: Historical or synthetic bars for this run.
            symbols: Symbols included in the feed.

        Returns:
            Run settings, out-of-sample metrics, and equity series for transport.

        Raises:
            ValueError: If there are too few bars or validation cannot form folds.
        """
        snapshots = feed.load()
        total = len(snapshots)
        folds_wanted = max(1, config.min_folds)
        test_bars = config.test or max(2, total // (folds_wanted + 1))
        train_bars = config.train or max(2, total - folds_wanted * test_bars)
        step_bars = config.step or test_bars
        if total < train_bars + test_bars:
            raise ResearchValidationError(
                f"{total} bars is too few for a {train_bars}/{test_bars} train/test split — "
                "sync more history or use smaller windows"
            )

        warnings: list[str] = []
        warmup_need = self._catalog.warmup_bars(config.strategy)
        if warmup_need and test_bars < warmup_need:
            warnings.append(
                f"test window is {test_bars} bars but {config.strategy} needs about "
                f"{warmup_need} bars of history before its rules fire — expect near-zero "
                "trades, which is not evidence of no edge. Use source=fetch for a longer history."
            )
        if train_bars < warmup_need:
            warnings.append(
                f"training window is {train_bars} bars, below the ~{warmup_need} bars this "
                f"strategy needs to warm up; parameters are being chosen from a window "
                "where the strategy can barely trade."
            )

        grid = self._catalog.parameter_grid(
            config.strategy,
            fast=config.fast,
            slow=config.slow,
            search=config.search,
        )
        warmup_bars = max(
            0,
            min(config.warmup if config.warmup is not None else 130, train_bars),
        )
        try:
            result = walk_forward(
                feed,
                strategy_class,
                grid,
                config=WalkForwardConfig(
                    train_bars=train_bars,
                    test_bars=test_bars,
                    step_bars=step_bars,
                    warmup_bars=warmup_bars,
                ),
                backtest=BacktestConfig(
                    initial_cash=config.cash,
                    slippage=slippage_model(bps=config.slippage_bps),
                    risk=RiskLimits(max_daily_loss=config.cash * 0.10),
                ),
                validation=ValidationConfig(
                    min_folds=config.min_folds,
                    min_trades=config.min_trades,
                    min_confidence=config.confidence,
                ),
            )
        except ValueError as error:
            raise ResearchValidationError(str(error)) from error
        if result.oos_metrics.num_trades == 0:
            warnings.append(
                "no trades were taken out of sample — the verdict below is a "
                "statement about the windows, not about the strategy."
            )

        first_test, last_test = result.folds[0].test_start, result.folds[-1].test_end
        benchmark_window = [
            snapshot for snapshot in snapshots if first_test <= snapshot.ts <= last_test
        ]
        benchmark_equity = buy_and_hold_equity(benchmark_window, config.cash)
        if len(benchmark_equity) < 2:
            warnings.append(
                "buy-and-hold comparison is unavailable because benchmark prices "
                "are incomplete for the unseen window."
            )
        return {
            "strategy": config.strategy,
            "source": config.source,
            "symbols": symbols,
            "bars": total,
            "train_bars": train_bars,
            "test_bars": test_bars,
            "step_bars": step_bars,
            "warmup_bars": warmup_bars,
            "n_trials": result.n_trials,
            "required_sharpe": result.required_sharpe,
            "deflated_sharpe": result.deflated_sharpe,
            "verdict": {
                "passed": result.verdict.passed,
                "checks": [
                    {"name": name, "ok": ok, "detail": detail}
                    for name, ok, detail in result.verdict.checks
                ],
            },
            "warnings": warnings,
            "oos": result.oos_metrics.as_dict(),
            "benchmark": (
                result.benchmark_metrics.as_dict()
                if result.benchmark_metrics is not None
                else None
            ),
            "folds": result.to_frame().to_dict(orient="records"),
            "equity_series": result.oos_equity,
            "benchmark_equity_series": benchmark_equity,
        }
