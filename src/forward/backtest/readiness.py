"""Inspect the same cached candles used by the runner, without executing trades."""
import ast

import pandas as pd

from forward.backtest.runner import BacktestError, index_by_ts

VOLUME_NAMES = {"volume", "vol_avg20", "session_vwap", "vwap_upper_1", "vwap_lower_1", "vwap_upper_2", "vwap_lower_2", "vwma", "mfi", "cmf", "efi", "eom", "obv", "ad"}


def check_readiness(runner):
    """Check required history and candle quality without creating a run.

    Args:
        runner: Configured backtest runner with market-data access.

    Returns:
        Readiness by symbol, plus any candle problems found.
    """
    symbols = runner.resolve_symbols()
    if not symbols:
        raise BacktestError("Choose at least one stock with cached history.", code="empty_universe", status=422)
    definition = runner._version_definition or {}
    from forward.strategy.definition import resolve_rules
    resolved = resolve_rules(definition or {"engine_key": runner.config.engine_key or runner.config.strategy, "params": runner.config.params})
    need = int(runner.config.warmup_bars or runner._warmup_need(runner.config.engine_key or runner.config.strategy) or 1)
    volume = False
    if resolved and resolved.ok:
        from forward.signals.requirements import formula_requirements
        for rules in (resolved.entry, resolved.exit_rules):
            need = max(need, int(rules.min_history_bars or 1))
            for key in ("custom_entry_formula", "custom_exit_formula", "rank_formula"):
                source = getattr(rules, key, None)
                if source:
                    need = max(need, formula_requirements(source)[1])
                    volume |= bool({n.id for n in ast.walk(ast.parse(source, mode="eval")) if isinstance(n, ast.Name)} & VOLUME_NAMES)
        volume |= resolved.entry.volume_multiple is not None and resolved.entry.setup != "custom_entry"
    frames, _ = runner.load_market_frames(symbols)
    issues, stocks = [], []
    for symbol in symbols:
        frame = frames.get(symbol)
        problem = None
        bars = 0
        if frame is None or frame.empty:
            problem = "No cached candles for this timeframe."
        else:
            frame = index_by_ts(frame)
            if runner.config.end:
                frame = frame.loc[frame.index < pd.Timestamp(runner.config.end) + pd.Timedelta(days=1)]
            scored = frame.loc[frame.index >= pd.Timestamp(runner.config.start)] if runner.config.start else frame
            bars = len(scored)
            missing = sorted({"open", "high", "low", "close"} - set(frame.columns))
            if missing:
                problem = f"Missing candle fields: {', '.join(missing)}."
            elif not bars:
                problem = "No candles in the selected dates."
            elif len(frame) <= need:
                problem = f"Needs {need} history bars plus a trading bar; only {len(frame)} are available."
            elif frame[["open", "high", "low", "close"]].apply(pd.to_numeric, errors="coerce").isna().any().any():
                problem = "Cached candles contain missing or invalid prices. Repair the history before testing."
            elif volume and ("volume" not in frame or pd.to_numeric(frame["volume"], errors="coerce").isna().any() or not pd.to_numeric(frame["volume"], errors="coerce").gt(0).any()):
                problem = "These rules require volume data; cached candles have none."
        stocks.append({"symbol": symbol, "bars": bars, "ready": problem is None})
        if problem:
            issues.append({"symbol": symbol, "message": problem})
    return {"ok": not issues, "required_history": need, "stocks": stocks, "issues": issues}
