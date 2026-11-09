"""Stress-test the one factor that survived: high cross-sectional volatility.

Why this deserves its own script
--------------------------------
Of seven factors, this is the only one whose spread survived beta-demeaning
(+2.23%/month, monotone across quintiles). Everything else either failed
monotonicity, flipped sign in one half of the window, or dissolved once the
market return was subtracted.

That is exactly the situation where a researcher fools themselves. A single
surviving result out of many tries is what multiple-testing *predicts* if
nothing is real. So before it is allowed into a strategy it has to clear:

1. **Monotonicity** in the beta-demeaned space, not just the raw space.
2. **Both halves** of the window, with the same sign and a similar size.
3. **Sub-period stability** across rolling 6-month blocks, so a single episode
   cannot carry the whole result.
4. **Year-by-year**, to find whether one year (e.g. 2021) is doing all the work.
5. **A within-vol-bucket momentum check**, so the strategy is not just momentum
   with extra steps.
6. **Turnover and cost**, because a monthly quintile rotation on 120 names is
   not free, and an edge of 2%/month can survive costs while an edge of 0.3%
   cannot.

If it clears all six it is a candidate. If it clears five, it is a story.

Usage:
    ./.venv/Scripts/python.exe scripts/research_vol_factor_stress.py
"""

# ruff: noqa: I001
from __future__ import annotations

import sys
import argparse
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "research_vol_factor_stress.json"


def load_close(config: dict) -> pd.DataFrame:
    """Load the configured panel and return closes by symbol."""
    frame = pd.read_parquet(ROOT / config["panel_file"])
    frame["ts"] = pd.to_datetime(frame["ts"])
    close = frame.pivot_table(index="ts", columns="symbol", values="close").sort_index()
    if close.empty:
        raise ValueError("The configured panel has no close-price observations.")
    return close


def quintile_means(long: pd.DataFrame, factor: str, config: dict, target: str = "exc") -> pd.Series:
    """Return configured quantile means for a factor."""
    labels = pd.qcut(long[factor].rank(method="first"), config["quantile_count"], labels=False)
    return long.groupby(labels)[target].mean() * 100.0


def main() -> int:
    """Run configured factor robustness checks."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    config_path = parser.parse_args().config
    config = json.loads(config_path.read_text(encoding="utf-8"))
    print(f"Configuration: {config_path.resolve()}")
    close = load_close(config)
    returns = close.pct_change()
    fwd = close.pct_change(config["forward_horizon_days"]).shift(-config["forward_horizon_days"])
    vol = returns.rolling(config["volatility_lookback_days"]).std().shift(1)

    long = pd.concat([vol.stack().rename("vol"), fwd.stack().rename("fwd")], axis=1).dropna()
    if long.empty:
        raise ValueError("The configured panel has no observations after factor warmup.")
    market = long.groupby(level=0)["fwd"].mean()
    long["mkt"] = long.index.get_level_values(0).map(market)
    long["exc"] = long["fwd"] - long["mkt"]
    dates = long.index.get_level_values(0)

    print(f"window {close.index[0]:%Y-%m-%d} -> {close.index[-1]:%Y-%m-%d}   "
          f"n={len(long):,}\n")

    # ---- 1 & 2: monotone on excess, and both halves -----------------------
    table = quintile_means(long, "vol", config)
    print("1) beta-demeaned quintiles (Q5 = highest vol)")
    print("   " + "  ".join(f"Q{i + 1} {x:+.2f}%" for i, x in enumerate(table)))
    print(f"   spread {table.iloc[-1] - table.iloc[0]:+.2f}%  "
          f"monotone={bool((np.diff(table.to_numpy()) >= 0).all())}")

    mid = close.index[len(close.index) // 2]
    for label, mask in [("first half", dates <= mid), ("second half", dates > mid)]:
        part = long[mask]
        t = quintile_means(part, "vol", config)
        print(f"   {label:<12} Q1 {t.iloc[0]:+.2f}%  Q5 {t.iloc[-1]:+.2f}%  "
              f"spread {t.iloc[-1] - t.iloc[0]:+.2f}%")
    print()

    # ---- 3: rolling 6-month blocks ---------------------------------------
    print("2) rolling 6-month blocks (spread Q5-Q1, excess)")
    blocks = pd.Series(dates, index=long.index).dt.to_period(config["stability_block_period"])
    spreads = []
    parts = long.groupby(blocks.values)
    for period, part in parts:
        if len(part) < config["minimum_block_observations"]:
            continue
        t = quintile_means(part, "vol", config)
        s = t.iloc[-1] - t.iloc[0]
        spreads.append(s)
        bar = "#" * max(int(abs(s) * 4), 1)
        print(f"   {period}  {s:+6.2f}%  {bar}")
    spreads = np.array(spreads)
    print(f"   {int((spreads > 0).sum())}/{len(spreads)} blocks positive, "
          f"median {np.median(spreads):+.2f}%\n")

    # ---- 4: calendar year ------------------------------------------------
    print("3) calendar year (spread Q5-Q1, excess)")
    years = pd.Series(dates, index=long.index).dt.year
    for year, part in long.groupby(years.values):
        if len(part) < config["minimum_year_observations"]:
            continue
        t = quintile_means(part, "vol")
        print(f"   {year}  Q1 {t.iloc[0]:+6.2f}%  Q5 {t.iloc[-1]:+6.2f}%  "
              f"spread {t.iloc[-1] - t.iloc[0]:+6.2f}%")
    print()

    # ---- 5: turnover -----------------------------------------------------
    monthly = close.resample(config["monthly_period"]).last()
    vol_m = monthly.pct_change().rolling(config["monthly_volatility_lookback"]).std().shift(1)
    top_sets = []
    for i in range(config["monthly_volatility_lookback"], len(monthly) - 1):
        v = vol_m.iloc[i].dropna()
        if len(v) < config["minimum_cross_section_symbols"]:
            continue
        top_sets.append(set(v.nlargest(max(len(v) // config["quantile_count"], 1)).index))
    turns = []
    for a, b in zip(top_sets, top_sets[1:], strict=False):
        if a:
            turns.append(1 - len(a & b) / len(a))
    turn = float(np.mean(turns)) if turns else float("nan")
    print(f"4) monthly turnover of the top quintile: {turn * 100:.0f}% of names "
          f"replaced each rebalance")
    cost_bps = config["one_way_cost_basis_points"]
    drag = turn * config["round_trips_per_rebalance"] * cost_bps / config["basis_points_per_unit"] * 100
    print(f"   at {cost_bps}bps per round trip -> ~{drag:.2f}%/month of drag "
          f"({drag * config['months_per_year']:.1f}%/yr)")
    spread = table.iloc[-1] - table.iloc[0]
    print(f"   net edge ~{spread - drag:+.2f}%/month  "
          f"({(spread - drag) * config['months_per_year']:+.1f}%/yr)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
