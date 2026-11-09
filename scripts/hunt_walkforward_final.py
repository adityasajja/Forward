"""Walk-forward validation on the best gap_vol config.

3-fold walk-forward across the OOS period, splitting into bull/sideways/bear
sub-periods. Also runs deflated Sharpe.
"""
# ruff: noqa: E402
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from hunt_final3 import (
    build_regime,
    load_stocks,
    load_symbol,
    run_local_strategy,
)  # noqa: E402
from strategy_hunt import parse_universe  # noqa: E402

p = print


def P(*a, **kw):
    kw.setdefault("flush", True)
    p(*a, **kw)

def main():
    """Run the configured final walk-forward comparison."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    config_path = parser.parse_args().config
    config = json.loads(config_path.read_text(encoding="utf-8"))
    P(f"Configuration: {config_path.resolve()}")
    start = pd.Timestamp(config["start_date"])
    end = pd.Timestamp(config["end_date"])

    all_symbols = list(set(
        parse_universe(ROOT / path) for path in config["universe_files"]
    ))
    P(f"Loading {len(all_symbols)} symbols...")
    panels, syms, dates = load_stocks(
        all_symbols, start, end, min_bars=config["minimum_bars"]
    )
    if not syms or len(dates) == 0:
        raise ValueError("Configured date range produced no usable stock data.")
    P(f"  {len(syms)} stocks, {len(dates)} dates")

    nift = load_symbol(config["benchmark_symbol"], start, end)
    aligned_benchmark = nift["close"].reindex(dates)
    if aligned_benchmark.isna().any():
        raise ValueError("Benchmark is missing dates required for regime signals.")
    nif_close = aligned_benchmark.to_numpy(dtype=np.float64)
    regime_200 = build_regime(nif_close, config["long_regime_window"])
    regime_50 = build_regime(nif_close, config["short_regime_window"])
    regime_dual = regime_50 & regime_200

    close = panels["close"].to_numpy(dtype=np.float64)
    high = panels["high"].to_numpy(dtype=np.float64)
    low = panels["low"].to_numpy(dtype=np.float64)
    open_ = panels["open"].to_numpy(dtype=np.float64)
    volume = panels["volume"].to_numpy(dtype=np.float64)
    n_dates = len(dates)
    mid = int(n_dates * config["oos_start_fraction"])

    # Compute gap_vol signal (same as hunt_final3.py)
    c = close
    o = open_
    v = volume
    momentum_lookback = config["momentum_lookback"]
    gap = np.full_like(c, -np.inf)
    gap[1:] = (o[1:] - c[:-1]) / c[:-1]
    gap = np.roll(gap, 1, axis=0)
    gap[:2] = -np.inf

    mom_5d = np.full_like(c, -np.inf)
    mom_5d[momentum_lookback:] = (
        c[momentum_lookback:] / c[:-momentum_lookback] - 1.0
    )
    mom_5d = np.roll(mom_5d, 1, axis=0)
    mom_5d[:momentum_lookback + 1] = -np.inf

    vol_avg = np.full_like(v, np.nan)
    for i in range(config["volume_lookback"], len(v)):
        vol_avg[i] = v[i - config["volume_lookback"]:i].mean(axis=0)
    measured_volume_average = vol_avg[config["volume_lookback"]:]
    if np.isnan(measured_volume_average).any() or (measured_volume_average <= 0).any():
        raise ValueError("Volume history is missing or nonpositive for the configured window.")
    vr = v / vol_avg

    gap_vol = np.where(
        (gap > config["gap_vol_minimum_gap"])
        & (vr > config["gap_vol_minimum_volume_ratio"]),
        gap * mom_5d * vr,
        -np.inf,
    )
    # Also compute gap_run_5 for comparison
    gap_run_5 = np.where(gap > config["gap_run_minimum_gap"], gap * mom_5d, -np.inf)

    # ── Walk-forward: 3 folds across OOS ──
    oos_start = mid
    oos_end = n_dates
    oos_len = oos_end - oos_start
    fold_size = oos_len // config["fold_count"]

    P(f"\n{'='*80}")
    P("WALK-FORWARD VALIDATION: configured gap_vol / dual strategy")
    P(f"{'='*80}")
    P(f"OOS window: {dates[oos_start].date()} → {dates[oos_end-1].date()}")

    for fi in range(config["fold_count"]):
        s_idx = oos_start + fi * fold_size
        e_idx = (oos_start + (fi + 1) * fold_size
                 if fi < config["fold_count"] - 1 else n_dates)

        fold_regime = regime_dual[s_idx:e_idx]

        r = run_local_strategy(
            close[s_idx:e_idx], high[s_idx:e_idx], low[s_idx:e_idx],
            gap_vol[s_idx:e_idx],
            sl_pct=config["walk_forward_strategy"]["stop"],
            tp_pct=config["walk_forward_strategy"]["target"],
            max_holding=config["walk_forward_strategy"]["holding"],
            k=config["walk_forward_strategy"]["positions"],
            step=config["entry_step"], costs=config["round_trip_cost"],
            regime_filter=fold_regime,
            vol_target=config["walk_forward_strategy"]["vol_target"],
        )

        if "error" in r:
            P(f"  Fold {fi+1} ({dates[s_idx].date()}→{dates[e_idx-1].date()}): {r['error']}")
        else:
            P(f"  Fold {fi+1} ({dates[s_idx].date()}→{dates[e_idx-1].date()}):")
            P(f"    Weekly mean:  {r['weekly_mean_pct']:+.3f}%")
            P(f"    Sharpe:       {r['sharpe']:.3f}")
            P(f"    DD:           {r['max_drawdown_pct']:.1f}%")
            P(f"    Rebalances:   {r['n_rebalances']}")
            P(f"    Win rate:     {r['weekly_win_rate_pct']}%")
            P(f"    P(>=2%):      {r['weeks_ge_2pct_pct']}%  P(>=5%): {r['weeks_ge_5pct_pct']}%")
            P(f"    Trades:       {r['num_trades']}  Stops: {r['stop_rate_pct']}%  Targets: {r['win_rate_pct']}%")


    # ── Full OOS for the best config + gap_run_5 comparison ──
    P(f"\n{'='*80}")
    P("FULL OOS COMPARISON")
    P(f"{'='*80}")

    signal_arrays = {"gap_vol": gap_vol, "gap_run_5": gap_run_5}
    regime_arrays = {
        "200d": regime_200,
        "dual": regime_dual,
        "none": None,
    }
    for sig_name in config["comparison_signals"]:
        sig = signal_arrays[sig_name]
        for reg_name in config["comparison_regimes"]:
            reg = regime_arrays[reg_name]
            for strategy in config["comparison_strategies"]:
                sl = strategy["stop"]
                tp = strategy["target"]
                mh = strategy["holding"]
                k = strategy["positions"]
                vt = strategy["vol_target"]
                if reg is not None:
                    r = run_local_strategy(
                        close[mid:], high[mid:], low[mid:], sig[mid:],
                        sl, tp, mh, k, config["entry_step"],
                        costs=config["round_trip_cost"],
                        regime_filter=reg[mid:], vol_target=vt,
                    )
                else:
                    r = run_local_strategy(
                        close[mid:], high[mid:], low[mid:], sig[mid:],
                        sl, tp, mh, k, config["entry_step"],
                        costs=config["round_trip_cost"],
                        vol_target=vt,
                    )
                if "error" in r:
                    continue
                if r["max_drawdown_pct"] < config["maximum_drawdown_pct"]:
                    continue
                if r["n_rebalances"] < config["minimum_rebalances"]:
                    continue
                P(f"  {sig_name}/{reg_name}/sl{sl}/tp{tp}/mh{mh}/k{k}/vt{vt}: "
                  f"wk={r['weekly_mean_pct']:+.3f}% Sharp={r['sharpe']:.2f} DD={r['max_drawdown_pct']:.0f}% "
                  f"rb={r['n_rebalances']} trades={r['num_trades']} win={r['weekly_win_rate_pct']:.0f}% "
                  f"P5%={r['weeks_ge_5pct_pct']:.0f}%")

    # ── Market regime classification ──
    P(f"\n{'='*80}")
    P("MARKET REGIME ANALYSIS")
    P(f"{'='*80}")

    # Classify each OOS day
    regime_states = []
    for i in range(mid, n_dates):
        if regime_dual[i]:
            regime_states.append(("bull", dates[i]))
        elif regime_200[i] or regime_50[i]:
            regime_states.append(("partial", dates[i]))
        else:
            regime_states.append(("bear", dates[i]))

    states = pd.DataFrame(regime_states, columns=["regime", "date"])
    counts = states["regime"].value_counts()
    P(f"  OOS regime breakdown ({oos_start-mid} days):")
    for state, count in counts.items():
        P(f"    {state}: {count} days ({100*count/len(states):.0f}%)")

    # ── 1-Minute backtest summary ──
    P(f"\n{'='*80}")
    P("1-MINUTE BACKTEST SUMMARY")
    P(f"{'='*80}")

    intraday_path = config["intraday_summary_file"]
    if intraday_path is not None:
        data = json.loads((ROOT / intraday_path).read_text(encoding="utf-8"))
        for r in data.get("results", []):
            if "error" in r:
                raise ValueError(
                    f"Configured intraday summary contains an error result: {r['error']}"
                )
            rb = r.get("n_rebalances", 0)
            marker = (
                " <<<"
                if r.get("sharpe", 0) > config["intraday_marker_minimum_sharpe"]
                and rb >= config["intraday_marker_minimum_rebalances"]
                else ""
            )
            P(f"  {r['label']:45s} ret={r['total_return_pct']:+.0f}% ann={r['annual_return_pct']:.0f}% "
              f"Sharpe={r['sharpe']:.3f} DD={r['max_drawdown_pct']:.0f}% "
              f"tr={r['n_trades']} rb={rb} win={r['win_rate_pct']:.0f}% "
              f"stop={r['stop_rate_pct']:.0f}% tgt={r['target_rate_pct']:.0f}%{marker}")

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
