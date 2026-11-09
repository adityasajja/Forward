"""Which "this stock is topping" conditions, if any, precede weak stretches?

Population: stock-days where the stock ran up (>=10% in 20 sessions or >=8% over its 50-day
average) and sits within 3.5% of its 20-day high. Within it, test single conditions and
report the average next-5-session return against the population's own average, separately
for an early and a late half of the history, so a result has to hold in both to count.

Run:  python scripts/study_exit_signals.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from forward.strategy.indicators import rsi

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "study_exit_signals.json"


def features(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Calculate the configured topping indicators for each daily bar.

    Args:
        df: Daily OHLCV frame for one symbol.
        config: Explicit indicator and signal thresholds.

    Returns:
        A frame of condition flags and future returns.
    """
    df = df.sort_values("ts").reset_index(drop=True)
    c, h, lo, o = (df[k].astype(float) for k in ("close", "high", "low", "open"))
    v = df["volume"].astype(float)
    span = (h - lo).replace(0, np.nan)
    ema = c.ewm(span=config["trend_ema_period"], adjust=False).mean()
    r = rsi(c, window=config["rsi_period"])
    out = pd.DataFrame({"ts": df["ts"]})
    momentum_days = config["momentum_days"]
    out["ret20"] = (c / c.shift(momentum_days[1]) - 1) * 100
    out["ext50"] = (c / c.rolling(config["trend_average_days"]).mean() - 1) * 100
    out["off_high"] = (c / h.rolling(config["high_lookback_days"]).max() - 1) * 100
    out["ret5"] = (c / c.shift(momentum_days[0]) - 1) * 100
    flat = config["flat"]
    out["flat"] = (
        (out["ret5"].abs() <= flat["maximum_absolute_return_pct"])
        & (
            span.rolling(flat["range_days"]).mean()
            / span.rolling(flat["range_baseline_days"]).mean()
            <= flat["maximum_range_ratio"]
        )
    )
    rsi_roll = config["rsi_roll"]
    out["rsi_roll"] = (
        (r.shift(rsi_roll["prior_days"]) >= rsi_roll["prior_minimum"])
        & (r.shift(rsi_roll["prior_days"]) - r >= rsi_roll["minimum_fall"])
    )
    volume_dry = config["volume_dry"]
    out["vol_dry"] = (
        v.rolling(volume_dry["recent_days"]).mean()
        / v.rolling(volume_dry["baseline_days"]).mean().replace(0, np.nan)
    ) <= volume_dry["maximum_ratio"]
    out["wicks"] = (
        ((h - np.maximum(o, c)) / span).rolling(config["wick_days"]).mean()
        >= config["minimum_upper_wick_ratio"]
    )
    out["big_run"] = out["ret20"] >= config["big_run_return_pct"]
    out["below_5d_low"] = c < lo.shift(1).rolling(config["recent_low_days"]).min()
    out["below_ema10"] = c < ema
    out["down_2d"] = (c < c.shift(1)) & (c.shift(1) < c.shift(2))
    out["gap_down"] = o < c.shift(1) * config["gap_down_multiplier"]
    out["fwd5"] = (c.shift(-momentum_days[0]) / c - 1) * 100
    population = config["population"]
    out["ran_top"] = (
        (out["ret20"] >= population["minimum_return_20_days_pct"])
        | (out["ext50"] >= population["minimum_extension_50_day_pct"])
    ) & (out["off_high"] >= population["minimum_off_high_pct"])
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    config_path = parser.parse_args().config
    config = json.loads(config_path.read_text(encoding="utf-8"))
    print(f"Configuration: {config_path.resolve()}")
    frames = []
    for path in sorted((ROOT / config["data_directory"]).glob("*.parquet")):
        df = pd.read_parquet(path)
        if len(df) < config["minimum_history_bars"]:
            continue
        f = features(df, config)
        f["sym"] = path.stem
        frames.append(f.dropna(subset=["fwd5", "ret20", "off_high"]))
    d = pd.concat(frames, ignore_index=True)
    pop = d[d["ran_top"]].copy()
    cut = pop["ts"].quantile(config["split_quantile"])
    early, late = pop[pop["ts"] <= cut], pop[pop["ts"] > cut]
    print(f"population: {len(pop):,} stock-days that ran up and sit at the top; split at {cut:%b %Y}")
    print(f"average next-5d return of the population: early {early['fwd5'].mean():.2f}%  late {late['fwd5'].mean():.2f}%\n")
    print(f"{'condition':26s} | {'early n':>8s} {'early 5d':>9s} {'edge':>6s} | {'late n':>8s} {'late 5d':>9s} {'edge':>6s} | verdict")
    for col in config["conditions"]:
        early_condition = early[early[col]]
        late_condition = late[late[col]]
        ee = early_condition["fwd5"].mean() - early["fwd5"].mean()
        le = late_condition["fwd5"].mean() - late["fwd5"].mean()
        edge = config["edge_threshold_pct"]
        verdict = "WEAK in both" if ee < -edge and le < -edge else ("strong in both" if ee > edge and le > edge else "no consistent edge")
        print(
            f"{col:26s} | {len(early_condition):8,d} "
            f"{early_condition['fwd5'].mean():9.2f} {ee:+6.2f} | "
            f"{len(late_condition):8,d} {late_condition['fwd5'].mean():9.2f} "
            f"{le:+6.2f} | {verdict}"
        )


if __name__ == "__main__":
    main()
