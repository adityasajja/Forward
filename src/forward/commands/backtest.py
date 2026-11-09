"""CLI commands for backtests and walk-forward research."""

from __future__ import annotations

from argparse import Namespace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from forward.data.base import DataFeed


def run_backtest(args: Namespace) -> int:
    """Run one backtest with the selected data source and strategy.

    Args:
        args: Parsed command-line options for the backtest command.

    Returns:
        The process exit code.
    """
    from forward.backtest.engine import BacktestConfig, BacktestEngine
    from forward.config.costs import slippage_model
    from forward.execution.risk import RiskLimits
    from forward.strategy.strategies import STRATEGIES

    symbols = tuple(s.strip().upper() for s in args.symbols.split(",") if s.strip())

    feed = build_market_feed(args, symbols=symbols, futures=args.futures)

    strategy_cls = STRATEGIES.get(args.strategy)
    if strategy_cls is None:
        logger.error("unknown strategy {!r}; available: {}", args.strategy, list(STRATEGIES))
        return 2
    strategy = (
        strategy_cls(fast=args.fast, slow=args.slow)
        if args.strategy == "sma_crossover"
        else strategy_cls()
    )

    config = BacktestConfig(
        initial_cash=args.cash,
        slippage=slippage_model(bps=args.slippage_bps),
        square_off_eod=args.square_off_eod,
        risk=RiskLimits(
            max_daily_loss=(
                args.max_daily_loss if args.max_daily_loss is not None else args.cash * 0.10
            )
        ),
    )
    result = BacktestEngine(feed, strategy, config).run()
    print(result.summary())
    if args.save:
        result.equity.to_csv(args.save, header=True)
        logger.info("wrote equity curve to {}", args.save)
    return 0


def run_research(args: Namespace) -> int:
    """Walk a strategy forward and report only the out-of-sample evidence.

    Args:
        args: Parsed command-line options for the research command.

    Returns:
        The process exit code.
    """
    from forward.backtest.engine import BacktestConfig
    from forward.config.costs import slippage_model
    from forward.execution.risk import RiskLimits
    from forward.research.validate import ValidationConfig, WalkForwardConfig, walk_forward
    from forward.strategy.strategies import STRATEGIES

    symbols = tuple(s.strip().upper() for s in args.symbols.split(",") if s.strip())

    feed = build_market_feed(args, symbols=symbols)

    strategy_cls = STRATEGIES.get(args.strategy)
    if strategy_cls is None:
        logger.error("unknown strategy {!r}; available: {}", args.strategy, list(STRATEGIES))
        return 2

    def grid(text: str) -> list[int]:
        return [int(x) for x in text.split(",") if x.strip()]

    # Parameter grids are strategy-specific; only SMA crossover takes fast/slow.
    param_grid = (
        {"fast": grid(args.fast), "slow": grid(args.slow)}
        if args.strategy == "sma_crossover"
        else {}
    )

    result = walk_forward(
        feed,
        strategy_cls,
        param_grid,
        config=WalkForwardConfig(train_bars=args.train, test_bars=args.test, step_bars=args.step),
        backtest=BacktestConfig(
            initial_cash=args.cash,
            slippage=slippage_model(),
            risk=RiskLimits(max_daily_loss=args.cash * 0.10),
        ),
        validation=ValidationConfig(min_trades=args.min_trades, min_confidence=args.confidence),
    )
    print(result.summary())
    if args.save:
        result.to_frame().to_csv(args.save, index=False)
        result.oos_equity.to_csv(args.save.replace(".csv", "_equity.csv"), header=True)
        logger.info("wrote per-fold results and OOS equity next to {}", args.save)
    return 0


def build_market_feed(
    args: Namespace, *, symbols: tuple[str, ...], futures: bool = False
) -> DataFeed:
    """Build one backtest feed from the explicitly selected source.

    Args:
        args: Parsed command arguments.
        symbols: Symbols selected for generated synthetic data.
        futures: Whether synthetic instruments should use futures settings.

    Returns:
        A feed backed by the selected market-data source.

    Raises:
        SystemExit: If a file-backed source lacks its path or instrument catalog.
    """
    if args.source == "synthetic":
        from forward.data.synthetic import SyntheticConfig, SyntheticFeed

        return SyntheticFeed(
            SyntheticConfig(
                symbols=symbols,
                start=datetime(2024, 1, 1, 9, 30),
                end=datetime(2024, 6, 28, 15, 59),
            ),
            futures=futures,
        )

    if not args.path:
        raise SystemExit(f"{args.source} input requires --path")
    if not args.instruments:
        raise SystemExit(f"{args.source} input requires --instruments with complete metadata")

    from forward.data.csv_feed import CsvFeed, ParquetFeed
    from forward.data.instrument_file import load_instruments

    instruments = load_instruments(args.instruments)
    path = Path(args.path)
    use_file = path.is_file()
    if args.source == "csv":
        if use_file:
            return CsvFeed(path=path, instruments=instruments)
        return CsvFeed(directory=path, instruments=instruments)
    if use_file:
        return ParquetFeed(path=path, instruments=instruments)
    return ParquetFeed(directory=path, instruments=instruments)
