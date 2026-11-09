"""Which buy setups and which market conditions have historical evidence behind them?

Wide matrices over every cached stock (dates x symbols), so market-wide measures (breadth,
the equal-weight market return, relative strength) come from the same data. Every result
is reported for an early and a late half of the history; only something that holds in both
should be trusted.

Run:  python scripts/study_buy_and_market.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "study_buy_and_market.json"


def load(config: dict) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Read all configured long-history files without hiding data errors.

    Args:
        config: Explicit data and history settings.

    Returns:
        Close, high, and volume panels keyed by symbol and date.
    """
    close, high, vol = {}, {}, {}
    data_directory = ROOT / config["data_directory"]
    for path in sorted(data_directory.glob("*.parquet")):
        df = pd.read_parquet(path, columns=["ts", "close", "high", "volume"])
        if len(df) < config["minimum_history_bars"]:
            continue
        df = df.drop_duplicates("ts").set_index("ts").sort_index()
        close[path.stem], high[path.stem], vol[path.stem] = df["close"], df["high"], df["volume"]
    return pd.DataFrame(close).sort_index(), pd.DataFrame(high).sort_index(), pd.DataFrame(vol).sort_index()


def summarise(name, mask, fwd5, fwd20, base5, base20, period, minimum_samples):
    m = mask & period
    n = int(m.sum().sum())
    if n < minimum_samples:
        return f"{name:34s} n={n:,}  (too few)"
    f5, f20 = fwd5[m].stack(), fwd20[m].stack()
    b5, b20 = base5[period.iloc[:, 0]].stack().mean(), base20[period.iloc[:, 0]].stack().mean()
    return (
        f"{name:34s} n={n:8,d} | 5d {f5.mean():+5.2f}% (edge {f5.mean() - b5:+5.2f}) "
        f"| 20d {f20.mean():+5.2f}% (edge {f20.mean() - b20:+5.2f}) hit {100 * (f20 > 0).mean():4.1f}%"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    config_path = parser.parse_args().config
    config = json.loads(config_path.read_text(encoding="utf-8"))
    print(f"Configuration: {config_path.resolve()}")
    close, high, vol = load(config)
    print(f"{close.shape[1]} stocks, {close.index.min():%b %Y} to {close.index.max():%b %Y}\n")
    first_momentum, second_momentum = config["momentum_days"]
    first_forward, second_forward = config["forward_days"]
    daily_return = close.pct_change(first_momentum) * 100
    ret20 = (close / close.shift(second_momentum) - 1) * 100
    fwd5 = (close.shift(-first_forward) / close - 1) * 100
    fwd20 = (close.shift(-second_forward) / close - 1) * 100
    sma50 = close.rolling(config["trend_days"]).mean()
    above50 = close > sma50
    hi252 = high.rolling(
        config["high_lookback_days"], min_periods=config["high_minimum_periods"]
    ).max()
    off252 = (close / hi252 - 1) * 100
    rvol = vol / vol.rolling(config["relative_volume_days"]).mean().shift(1)
    prior_hi20 = high.rolling(config["breakout_days"]).max().shift(1)

    mkt20 = ret20.mean(axis=1)
    rs = ret20.sub(mkt20, axis=0)  # relative strength against the equal-weight market
    breadth = above50.where(close.notna()).mean(axis=1) * 100

    valid = close.notna() & fwd5.notna() & fwd20.notna() & ret20.notna() & sma50.notna() & off252.notna()
    base5 = fwd5.where(valid)
    base20 = fwd20.where(valid)
    cut = close.index[int(len(close.index) * config["split_fraction"])]
    early = pd.DataFrame(np.repeat((close.index <= cut)[:, None], close.shape[1], axis=1), index=close.index, columns=close.columns)
    late = ~early

    setup = config["setups"]
    setups = {
        "all stock-days (baseline)": valid,
        "breakout: new high with volume": valid & (close > prior_hi20) & (rvol >= setup["breakout_volume_ratio"]),
        "leader resting after pullback": (
            valid & (rs >= setup["leader_resting_relative_strength"]) & above50
            & (off252 >= setup["leader_resting_off_high_min_pct"])
            & (off252 <= setup["leader_resting_off_high_max_pct"])
            & (daily_return >= setup["leader_resting_daily_return_min_pct"])
            & (daily_return <= setup["leader_resting_daily_return_max_pct"])
        ),
        "leader near recent highs": (
            valid & (rs >= setup["leader_at_high_relative_strength"])
            & above50 & (off252 >= setup["leader_at_high_max_off_pct"])
        ),
        "strong medium-term run": valid & (rs >= setup["strong_run_relative_strength"]),
        "oversold medium-term move": valid & (ret20 <= setup["oversold_return_pct"]),
    }
    for label, period in (("EARLY half", early), ("LATE half ", late)):
        print(f"--- {label}")
        for name, mask in setups.items():
            print(
                summarise(
                    name, mask, fwd5, fwd20, base5, base20, period,
                    config["minimum_samples"],
                )
            )
        print()

    # Market scenario: what has followed a given state of breadth? (equal-weight market)
    m5 = fwd5.where(close.notna()).mean(axis=1)
    m20 = fwd20.where(close.notna()).mean(axis=1)
    table = pd.DataFrame({"breadth": breadth, "m5": m5, "m20": m20}).dropna()
    table["half"] = np.where(table.index <= cut, "early", "late")
    bins = config["breadth_bins"]
    labels = config["breadth_labels"]
    table["bucket"] = pd.cut(table["breadth"], bins=bins, labels=labels, right=False)
    print("Market: equal-weight return over the NEXT 20 sessions by today's breadth (% above 50-day avg)")
    print(f"{'breadth':8s} | {'early days':>10s} {'20d avg':>8s} | {'late days':>10s} {'20d avg':>8s}")
    for lab in labels:
        e = table[(table["bucket"] == lab) & (table["half"] == "early")]
        late_rows = table[(table["bucket"] == lab) & (table["half"] == "late")]
        print(
            f"{lab:8s} | {len(e):10,d} {e['m20'].mean():+8.2f} | "
            f"{len(late_rows):10,d} {late_rows['m20'].mean():+8.2f}"
        )
    print(f"{'all':8s} | {int((table['half'] == 'early').sum()):10,d} {table[table['half'] == 'early']['m20'].mean():+8.2f} | "
          f"{int((table['half'] == 'late').sum()):10,d} {table[table['half'] == 'late']['m20'].mean():+8.2f}")


if __name__ == "__main__":
    main()
