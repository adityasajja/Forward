"""Paper trading monitor for the gap-and-run strategy.

Runs daily checks using cached daily data:
1. Checks NIFTY regime (50d AND 200d SMA)
2. If bullish, applies the gap_vol signal and selects top-k stocks
3. Outputs trade suggestions (entries and exits)
4. Saves results to data/research/paper_trade_log.json

Usage:
    python scripts/paper_trade.py
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from hunt_final3 import build_regime, load_stocks, run_local_strategy  # noqa: E402
from strategy_hunt import parse_universe  # noqa: E402

CONFIG_PATH = ROOT / "config" / "paper_trade.json"


def main() -> int:
    """Run the configured gap-and-run paper monitor once."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    print(f"Configuration: {args.config.resolve()}")

    start = pd.Timestamp(config["out_of_sample_start_date"])

    # Load universe
    all_symbols = list(
        dict.fromkeys(
            symbol
            for relative_path in config["universe_files"]
            for symbol in parse_universe(ROOT / relative_path)
        )
    )
    benchmark_symbol = config["benchmark_symbol"]
    if benchmark_symbol not in all_symbols:
        all_symbols.append(benchmark_symbol)

    P(f"Loading {len(all_symbols)} symbols for paper trading monitor...")
    # Load FULL data (2020-2026) so regime SMAs are correct, then slice at OOS
    full_start = pd.Timestamp(config["history_start_date"])
    full_end = pd.Timestamp(config["history_end_date"])
    panels, syms, full_dates = load_stocks(
        all_symbols,
        full_start,
        full_end,
        min_bars=config["minimum_history_bars"],
    )

    if len(syms) < config["minimum_symbol_count"]:
        raise ValueError("Loaded symbol count is below the configured minimum.")

    # Find OOS start index
    oos_start_idx = 0
    for i, d in enumerate(full_dates):
        if d >= start:
            oos_start_idx = i
            break
    else:
        raise ValueError("Configured out-of-sample start is outside loaded history.")
    P(
        f"Loaded: {len(syms)} stocks, {len(full_dates)} total dates "
        f"(OOS from {full_dates[oos_start_idx].date()})"
    )

    close = panels["close"].to_numpy(dtype=np.float64)
    high = panels["high"].to_numpy(dtype=np.float64)
    low = panels["low"].to_numpy(dtype=np.float64)
    open_ = panels["open"].to_numpy(dtype=np.float64)
    volume = panels["volume"].to_numpy(dtype=np.float64)

    # NIFTY regime (using original hunt_final3 functions)
    if benchmark_symbol not in syms:
        raise ValueError(f"Benchmark symbol {benchmark_symbol!r} is not in loaded history.")
    nidx = syms.index(benchmark_symbol)
    n_close = close[:, nidx]
    short_regime_period, long_regime_period = config["regime_periods"]
    n_regime_short = build_regime(n_close, short_regime_period)
    n_regime_long = build_regime(n_close, long_regime_period)
    n_regime_dual = n_regime_short & n_regime_long
    last = len(n_close) - 1
    short_average = float(np.nanmean(n_close[max(0, last - short_regime_period + 1) : last + 1]))
    long_average = float(np.nanmean(n_close[max(0, last - long_regime_period + 1) : last + 1]))
    regime_info = {
        "current_price": float(n_close[last]),
        "short_average": short_average,
        "long_average": long_average,
        "above_short_average": bool(n_regime_short[last]),
        "above_long_average": bool(n_regime_long[last]),
        "dual_regime": bool(n_regime_dual[last]),
        "regime_days": int(n_regime_dual.sum()),
        "total_days": int(len(n_close)),
        "pct_regime_days": round(100 * n_regime_dual.sum() / len(n_close), 1),
    }

    # Compute gap_vol signal (matching hunt_final3.py exactly)
    c = close
    o = open_
    v = volume
    gap = np.full_like(c, -np.inf)
    gap[1:] = (o[1:] - c[:-1]) / c[:-1]
    gap = np.roll(gap, 1, axis=0)
    gap[:2] = -np.inf

    mom_5d = np.full_like(c, -np.inf)
    momentum_period = config["momentum_period"]
    mom_5d[momentum_period:] = c[momentum_period:] / c[:-momentum_period] - 1.0
    mom_5d = np.roll(mom_5d, 1, axis=0)
    mom_5d[: momentum_period + 1] = -np.inf

    vol_avg = np.full_like(v, np.nan)
    volume_period = config["volume_average_period"]
    for i in range(volume_period, len(v)):
        vol_avg[i] = v[i - volume_period : i].mean(axis=0)
    vr = np.divide(
        v,
        vol_avg,
        out=np.full_like(v, np.nan),
        where=vol_avg > 0,
    )

    gap_vol = np.where(
        (gap > config["gap_return_threshold"]) & (vr > config["volume_ratio_threshold"]),
        gap * mom_5d * vr,
        -np.inf,
    )

    P(f"\n{'=' * 80}")
    P("PAPER TRADING MONITOR - gap_and_run strategy")
    P(f"{'=' * 80}")
    P(f"\nRegime Check (as of {full_dates[-1].strftime('%Y-%m-%d')}):")
    P(f"  {benchmark_symbol} Price: {regime_info['current_price']:.2f}")
    P(
        f"  {short_regime_period}d average: {short_average:.2f} "
        f"({'ABOVE' if regime_info['above_short_average'] else 'BELOW'})"
    )
    P(
        f"  {long_regime_period}d average: {long_average:.2f} "
        f"({'ABOVE' if regime_info['above_long_average'] else 'BELOW'})"
    )
    P(f"  Dual Regime: {'BULL' if regime_info['dual_regime'] else 'BEAR/SIDEWAYS'}")
    if regime_info.get("regime_days") is not None:
        P(
            f"  Regime coverage: {regime_info['regime_days']}/{regime_info['total_days']} days "
            f"({regime_info['pct_regime_days']}%)"
        )

    # Run strategy on OOS slice (signals/regime computed on full data)
    oos_dates = full_dates[oos_start_idx:]
    run_results = []
    for run_config in config["strategy_runs"]:
        result = run_local_strategy(
            close[oos_start_idx:],
            high[oos_start_idx:],
            low[oos_start_idx:],
            gap_vol[oos_start_idx:],
            sl_pct=run_config["stop_pct"],
            tp_pct=run_config["target_pct"],
            max_holding=run_config["maximum_holding_days"],
            k=run_config["top_positions"],
            step=run_config["rebalance_step"],
            costs=config["round_trip_cost_fraction"],
            regime_filter=n_regime_dual[oos_start_idx:],
            vol_target=run_config["annual_volatility_target"],
        )
        run_results.append(result)

    for run_config, result in zip(config["strategy_runs"], run_results, strict=True):
        P(f"\nPaper Trading Results ({run_config['name']}):")
        P(f"  Period: {oos_dates[0].strftime('%Y-%m-%d')} → {oos_dates[-1].strftime('%Y-%m-%d')}")
        if "error" in result:
            P(f"  Result: {result['error']}")
        else:
            for key, value in result.items():
                P(f"  {key}: {value}")

    # Save log
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "period": [str(oos_dates[0].date()), str(oos_dates[-1].date())],
        "regime": regime_info,
        "strategy_runs": [
            {"name": run_config["name"], **result}
            for run_config, result in zip(config["strategy_runs"], run_results, strict=True)
        ],
    }
    log_path = ROOT / config["log_file"]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logs = json.loads(log_path.read_text(encoding="utf-8")) if log_path.exists() else []
    logs.append(log_entry)
    log_path.write_text(json.dumps(logs, indent=2, default=str), encoding="utf-8")
    P(f"\nLog saved to {log_path}")

    # Recommendation
    P(f"\n{'=' * 80}")
    P("RECOMMENDATION:")
    P(f"{'=' * 80}")
    if regime_info["dual_regime"]:
        P("  ✓ Market is in BULL regime. Strategy is ACTIVE.")
        P(
            f"  → Apply gap_vol signal and select top-{config['strategy_runs'][0]['top_positions']} stocks"
        )
    else:
        P("  ✗ Market is in BEAR/SIDEWAYS regime. Strategy is INACTIVE.")
        P("  → Keep portfolio in cash. Wait for dual regime to turn positive.")
        P("  → Keep the strategy inactive until the configured regime turns positive.")

    return 0


def P(*a, **k):
    """Print a progress message immediately."""
    k.setdefault("flush", True)
    print(*a, **k)


if __name__ == "__main__":
    raise SystemExit(main())
