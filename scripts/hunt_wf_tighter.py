"""Walk-forward validation with tighter SL configs."""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from hunt_final3 import build_regime, load_stocks, load_symbol, run_local_strategy  # noqa: E402
from strategy_hunt import parse_universe  # noqa: E402

p = print


def P(*a, **kw):
    kw.setdefault("flush", True)
    p(*a, **kw)

def main():
    """Compare the configured tighter-stop walk-forward grids."""
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

    nift = load_symbol("NIFTYBEES", start, end)
    aligned_nifty = nift["close"].reindex(dates)
    if aligned_nifty.isna().any():
        raise ValueError("NIFTYBEES is missing dates required for regime signals.")
    nc = aligned_nifty.to_numpy(dtype=np.float64)
    r200 = build_regime(nc, config["long_regime_window"])
    r50 = build_regime(nc, config["short_regime_window"])
    reg_dual = r50 & r200
    reg_200 = r200

    close = panels["close"].to_numpy(dtype=np.float64)
    high = panels["high"].to_numpy(dtype=np.float64)
    low = panels["low"].to_numpy(dtype=np.float64)
    open_ = panels["open"].to_numpy(dtype=np.float64)
    vol = panels["volume"].to_numpy(dtype=np.float64)
    n = len(dates)
    mid = int(n * config["oos_fraction"])

    gap = np.full_like(close, -np.inf)
    gap[1:] = (open_[1:] - close[:-1]) / close[:-1]
    gap = np.roll(gap, 1, axis=0)
    gap[:config["gap_signal_warmup_bars"]] = -np.inf

    momentum_lookback = config["momentum_lookback"]
    mom = np.full_like(close, -np.inf)
    mom[momentum_lookback:] = (
        close[momentum_lookback:] / close[:-momentum_lookback] - 1.0
    )
    mom = np.roll(mom, 1, axis=0)
    mom[:config["momentum_signal_warmup_bars"]] = -np.inf

    vavg = np.full_like(vol, np.nan)
    for i in range(config["volume_lookback"], len(vol)):
        vavg[i] = vol[i - config["volume_lookback"]:i].mean(axis=0)
    measured_average = vavg[config["volume_lookback"]:]
    if np.isnan(measured_average).any() or (measured_average <= 0).any():
        raise ValueError("Volume history is missing or nonpositive in the configured window.")
    vr = vol / vavg

    gap_vol = np.where(
        (gap > config["gap_vol_minimum_gap"])
        & (vr > config["gap_vol_minimum_volume_ratio"]),
        gap * mom * vr,
        -np.inf,
    )
    gap_run_5 = np.where(gap > config["gap_run_minimum_gap"], gap * mom, -np.inf)

    fold_size = (n - mid) // config["fold_count"]

    P(f"\n{'='*70}")
    P("WALK-FORWARD: gap_vol / dual / tighter SL configs")
    P(f"{'='*70}")
    for group in config["experiment_groups"]:
        for label, params in group["strategies"].items():
            sl, tp, mh, k = (
                params[key] for key in ("stop", "target", "holding", "positions")
            )
            for fi in range(config["fold_count"]):
                si = mid + fi * fold_size
                ei = mid + (fi + 1) * fold_size if fi < config["fold_count"] - 1 else n
                signal = gap_vol if group["signal"] == "gap_vol" else gap_run_5
                regime = {"dual": reg_dual, "long": reg_200}[group["regime"]]
                r = run_local_strategy(
                    close[si:ei], high[si:ei], low[si:ei], signal[si:ei],
                    sl, tp, mh, k, config["entry_step"],
                    costs=config["round_trip_cost"],
                    regime_filter=regime[si:ei],
                    vol_target=config["annual_volatility_target"]
                )
                if "error" in r:
                    P(f"  {label} F{fi+1}: {r['error']}")
                else:
                    P(f"  {label} F{fi+1}: wk={r['weekly_mean_pct']:+.3f}% S={r['sharpe']:.2f} "
                      f"DD={r['max_drawdown_pct']:.0f}% rb={r['n_rebalances']} win={r['weekly_win_rate_pct']:.0f}%")

    P("\nDone!")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
