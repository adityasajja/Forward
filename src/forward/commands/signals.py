"""CLI commands for signal scanning and validation."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from loguru import logger

from forward.commands.broker import authenticated_client


def run_signals(args: Namespace) -> int:
    """Run signal scanning, validation, or configuration setup.

    Args:
        args: Parsed command-line options for the signals command.

    Returns:
        The process exit code.
    """
    import dataclasses
    import json as _json

    import polars as pl

    from forward.brokers.iifl.signal_market import IiflSignalMarketAdapter
    from forward.config.settings import research_data_root
    from forward.services.signal_scanning import SignalScanningService
    from forward.signals.models import DEFAULT_CONFIG_PATH, ScanResult, SignalConfig
    from forward.signals.report import format_report

    if args.action == "init-config":
        SignalConfig().save(DEFAULT_CONFIG_PATH)
        print(f"wrote default thresholds to {DEFAULT_CONFIG_PATH}")
        print("These are starting points, not findings. Validate before trusting them.")
        return 0

    try:
        cfg = SignalConfig.load()
    except FileNotFoundError:
        logger.error("signal config is missing; run `forward signals init-config` first")
        return 2

    if args.action == "validate":
        from forward.backtest.engine import BacktestConfig
        from forward.core.enums import Timeframe
        from forward.core.feeds import ListFeed
        from forward.core.instrument_models import Instrument
        from forward.core.market_universe import DEFAULT_SCAN_UNIVERSE
        from forward.data.base import pivot_to_snapshots
        from forward.research.validate import ValidationConfig, WalkForwardConfig, walk_forward
        from forward.signals.strategy import SignalEntryStrategy

        symbols = cfg.universe or list(DEFAULT_SCAN_UNIVERSE)
        # Fetch real history rather than reading the cache: the local cache
        # holds about a year, which is too short to carve into folds that also
        # leave room for indicator warmup.
        client = authenticated_client()
        if client is None:
            return 1
        from forward.brokers.iifl.daily_history import load_daily

        master = None
        frames = []
        with client:
            from forward.brokers.iifl.contracts import InstrumentMaster

            master = InstrumentMaster(client)
            master.load_cached([cfg.exchange])
            for symbol in symbols:
                try:
                    instrument = master.find(symbol, cfg.exchange)
                except KeyError:
                    continue
                frame = load_daily(client, instrument, lookback_days=2200)
                if frame.empty:
                    continue
                frame = pl.from_pandas(frame).with_columns(pl.lit(symbol).alias("symbol"))
                frames.append(frame)
                logger.info("{}: {} daily bars", symbol, len(frame))
        if not frames:
            logger.error("no daily history available")
            return 1

        combined = pl.concat(frames, how="diagonal_relaxed")
        snapshots = pivot_to_snapshots(combined, Timeframe.DAY_1)
        instruments = {s: Instrument(symbol=s, exchange=cfg.exchange) for s in symbols}
        feed = ListFeed(snapshots, instruments)

        params = {
            **dataclasses.asdict(cfg.entries),
            **dataclasses.asdict(cfg.exits),
            "allocation": 0.10,
        }
        # One combination = a single test. A grid = a search, and the deflated
        # Sharpe then has to clear a hurdle scaled to how many were tried.
        grid = {k: [v] for k, v in params.items()}
        if args.search:
            from forward.signals.models import SEARCH_GRID

            grid.update(SEARCH_GRID)
            combos = 1
            for values in grid.values():
                combos *= len(values)
            logger.info("sweeping {} parameter combinations", combos)

        result = walk_forward(
            feed,
            SignalEntryStrategy,
            grid,
            config=WalkForwardConfig(
                train_bars=args.train,
                test_bars=args.test,
                # Enough history for the longest indicator to be valid before
                # the first scored bar, or the strategy never trades at all.
                warmup_bars=cfg.entries.min_history_bars + 60,
            ),
            backtest=BacktestConfig(initial_cash=1_000_000.0),
            validation=ValidationConfig(min_folds=2, min_trades=5),
        )
        print(result.summary())
        out = research_data_root() / "signals" / "validation.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            _json.dumps(
                {
                    "passed": result.verdict.passed,
                    "oos_sharpe": result.oos_metrics.sharpe,
                    "deflated_sharpe": result.deflated_sharpe,
                    "folds": len(result.folds),
                    "trades": result.oos_metrics.num_trades,
                    "checks": [
                        {"name": n, "ok": ok, "detail": d} for n, ok, d in result.verdict.checks
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        logger.info("wrote {}", out)
        return 0 if result.verdict.passed else 1

    # Whether the entry rules have actually passed out-of-sample validation.
    # Read from disk rather than assumed, so the report can never quietly
    # present an unproven rule as though it were established.
    validated = False
    validation_path = research_data_root() / "signals" / "validation.json"
    if validation_path.exists():
        try:
            validated = bool(_json.loads(validation_path.read_text(encoding="utf-8")).get("passed"))
        except Exception:  # noqa: BLE001 - a corrupt file just means "not validated"
            validated = False

    client = authenticated_client()
    if client is None:
        return 1

    result = ScanResult()
    with client:
        from forward.config.settings import get_settings

        settings = get_settings()
        scanner = SignalScanningService(
            IiflSignalMarketAdapter(client),
            lookback_days=settings.signal_scan_lookback_days,
            max_candidates=settings.signal_scan_max_candidates,
        )
        if args.action in ("scan", "sell"):
            sells, errors = scanner.scan_holdings(cfg)
            result.sells = sells
            result.errors.extend(errors)
        if args.action in ("scan", "buy"):
            symbols = args.symbols or ",".join(cfg.universe)
            from forward.core.market_universe import DEFAULT_SCAN_UNIVERSE

            watchlist = [
                s.strip().upper()
                for s in (symbols.split(",") if symbols else DEFAULT_SCAN_UNIVERSE)
                if s.strip()
            ]
            buys, errors = scanner.scan_universe(watchlist, cfg)
            result.buys = buys
            result.errors.extend(errors)

    title, body = format_report(result, validated=validated)
    print(title)
    print(body)
    for error in result.errors[:10]:
        logger.warning(error)

    if args.json_path:
        Path(args.json_path).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_path).write_text(
            _json.dumps(
                {
                    "buys": [dataclasses.asdict(s) for s in result.buys],
                    "sells": [dataclasses.asdict(s) for s in result.sells],
                    "errors": result.errors,
                },
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        logger.info("wrote signals to {}", args.json_path)

    if args.telegram:
        from forward.alerts.channels import TelegramChannel
        from forward.config.settings import get_settings

        settings = get_settings()
        channel = TelegramChannel(settings.telegram_bot_token, settings.telegram_chat_id)
        if channel.send(title, body):
            logger.info("pushed to telegram")
        else:
            logger.error("telegram send failed — check TELEGRAM_BOT_TOKEN / CHAT_ID")
            return 1
    return 0
