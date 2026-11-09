"""Does any chart pattern raise the odds of a 2%+ week, in both halves of the history?

Every Friday, for every stock, note which patterns are present at the close and
record whether the *next* week gained 2% or more. A pattern is only interesting if
it lifts that probability above what the stock's own volatility already explains,
in both the early and the late half, and survives the fact that dozens were tried.

Why volatility is controlled for: a stock that moves 6% a week will clear +2% far
more often than one that moves 1.5%, whatever pattern it shows. Without that control
the study would "discover" that jumpy stocks have more big weeks.

The stock universe is today's long-history names, so failed companies are absent.
That inflates absolute levels; the comparisons here are within one universe.

Run:  ./.venv/Scripts/python.exe scripts/study_chart_patterns.py
"""

from __future__ import annotations

import argparse
import json
from math import sqrt
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd

from forward.data.cached_market_frames import CachedMarketFramesAdapter
from forward.research.hunt import Costs, stock_universe

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "study_chart_patterns.json"


def features(
    close: pd.DataFrame,
    high: pd.DataFrame,
    low: pd.DataFrame,
    vol: pd.DataFrame,
    config: dict,
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """Build configured pattern flags and expected weekly moves.

    Args:
        close: Daily close prices by symbol.
        high: Daily high prices by symbol.
        low: Daily low prices by symbol.
        vol: Daily volume by symbol.
        config: Explicit indicator and pattern settings.

    Returns:
        Pattern flags and expected weekly move estimates.
    """
    ema_short, ema_long = config["ema_periods"]
    volume_short, volume_long = config["volume_periods"]
    high_short, high_long = config["high_periods"]
    rsi_thresholds = config["rsi_thresholds"]
    ret = close.pct_change()
    ema_fast = close.ewm(span=ema_short).mean()
    ema_slow = close.ewm(span=ema_long).mean()
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / config["rsi_period"]).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / config["rsi_period"]).mean()
    rsi = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    vol_ratio = vol.rolling(volume_short).mean() / vol.rolling(volume_long).mean()
    hi_long = close.rolling(high_long).max()
    hi_short = close.shift(1).rolling(high_short).max()
    span = (
        high.rolling(config["range_period_days"]).max()
        - low.rolling(config["range_period_days"]).min()
    ) / close
    gap_up = ret > config["daily_gap_return_fraction"]
    four_week_return = close / close.shift(config["expected_move_lookback_days"]) - 1
    prior_week_return = close / close.shift(config["forward_days"]) - 1
    return {
        "new_20d_high": close > hi_short,
        "within_3pct_of_52w_high": close >= config["high_proximity_fraction"] * hi_long,
        "new_52w_high": close >= hi_long,
        "uptrend_ema20>ema50": (close > ema_fast) & (ema_fast > ema_slow),
        "below_ema50": close < ema_slow,
        "rsi_below_30": rsi < rsi_thresholds["oversold"],
        "rsi_above_70": rsi > rsi_thresholds["overbought"],
        "rsi_50_to_65": (
            (rsi > rsi_thresholds["middle_minimum"])
            & (rsi < rsi_thresholds["middle_maximum"])
        ),
        "volume_spike_2x": vol_ratio > config["volume_spike_ratio"],
        "up_gt_2pct_day_this_week": gap_up.rolling(config["forward_days"]).max().astype(bool),
        "tight_10d_range_lt5pct": span < config["maximum_tight_range_fraction"],
        "down_10pct_in_4w": four_week_return < -config["four_week_return_fraction"],
        "up_10pct_in_4w": four_week_return > config["four_week_return_fraction"],
        "up_3pct_last_week": prior_week_return > config["prior_week_return_fraction"],
        "down_3pct_last_week": prior_week_return < -config["prior_week_return_fraction"],
        "breakout_on_volume": (
            (close > hi_short) & (vol_ratio > config["breakout_volume_ratio"])
        ),
        "squeeze_then_breakout": (
            (span.shift(1) < config["squeeze_range_fraction"])
            & (close > hi_short)
        ),
    }, ret.rolling(config["expected_move_lookback_days"]).std() * np.sqrt(
        config["expected_move_period_days"]
    )


def build(
    history: CachedMarketFramesAdapter,
    config: dict,
) -> tuple[pd.DataFrame, dict[str, pd.Series], object]:
    """Build stock-week outcomes, pattern flags, and the midpoint date.

    Args:
        history: Adapter that reads cached daily market frames.
        config: Explicit history and pattern settings.

    Returns:
        Stock-week outcomes, pattern flags, and the midpoint date.
    """
    names = stock_universe(history, min_bars=config["minimum_symbol_history_bars"])
    cached = history.frames(config["exchange"], names)
    frames = {}
    for kind, column in (("close", "close"), ("high", "high"), ("low", "low"), ("volume", "volume")):
        cols = []
        for s in names:
            df = cached[s][["ts", column]].dropna()
            df = df[df[column] > 0].drop_duplicates("ts").set_index("ts")[column].rename(s)
            cols.append(df)
        frames[kind] = pd.concat(cols, axis=1, sort=True).loc[config["history_start_date"]:]
    close, high, low, volume = frames["close"], frames["high"], frames["low"], frames["volume"]
    # Drop the corrupt-bar spikes the broker cache carries, so they cannot fake huge weeks.
    from forward.research.data_hygiene import reverting_spike_mask

    for s in close.columns:
        series = close[s]
        present = series.dropna()
        bad = reverting_spike_mask(present).to_numpy()
        close.loc[present.index[bad], s] = np.nan

    flags, weekly_move = features(close, high, low, volume, config)
    # Each week's last trading day. Broker bars are stamped 09:15, so calendar
    # Friday midnight labels never appear in the index.
    fridays = list(
        close.groupby(close.index.to_period(config["week_period"])).tail(1).index
    )
    nxt = close.shift(-config["forward_days"])
    fwd = (nxt / close - 1).loc[fridays]
    move = weekly_move.loc[fridays]

    # A stock-week counts only when both the flags and the outcome are defined.
    valid = fwd.notna() & move.notna()
    stacked = pd.DataFrame({"fwd": fwd.where(valid).stack(), "move": move.where(valid).stack()})
    stacked["hit"] = stacked["fwd"] >= config["hit_return_fraction"]
    stacked["date"] = stacked.index.get_level_values(0)
    bucket = pd.qcut(
        stacked["move"], config["volatility_buckets"], labels=False, duplicates="drop"
    )
    stacked["vol_bucket"] = bucket
    # What volatility alone predicts: the hit rate of stocks that move about this much.
    stacked["expected_hit"] = stacked.groupby("vol_bucket")["hit"].transform("mean")
    split_index = int(len(stacked) * config["split_quantile"])
    mid = stacked["date"].sort_values().iloc[split_index]
    columns = {
        name: flag.loc[fridays].where(valid).stack().reindex(stacked.index).fillna(False).astype(bool)
        for name, flag in flags.items()
    }
    return stacked, columns, mid


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    config_path = parser.parse_args().config
    config = json.loads(config_path.read_text(encoding="utf-8"))
    print(f"Configuration: {config_path.resolve()}")
    cost_model = Costs(**config["costs"])
    round_trip_cost = cost_model.round_trip_pct(config["cost_notional"]) / 100
    output_path = ROOT / config["output_file"]
    stacked, columns, mid = build(CachedMarketFramesAdapter(), config)
    base = float(stacked["hit"].mean())
    flags = columns
    n_tests = len(flags)
    z_needed = NormalDist().inv_cdf(
        1 - config["significance_alpha"] / (2 * n_tests)
    )

    rows = []
    for name, flag in flags.items():
        f = flag
        sel = stacked[f]
        if len(sel) < config["minimum_selected_weeks"]:
            continue

        def measure(part: pd.DataFrame) -> dict:
            n = len(part)
            if n < config["minimum_split_weeks"]:
                return {
                    "n": n,
                    "p_hit": None,
                    "p_hit_expected_from_volatility": None,
                    "lift_pts": None,
                    "z_vs_volatility_only": None,
                    "mean_next_week_net_pct": None,
                }
            p = float(part["hit"].mean())
            exp = float(part["expected_hit"].mean())
            se = sqrt(max(exp * (1 - exp), 1e-9) / n)
            net = float(part["fwd"].mean() - round_trip_cost)
            return {
                "n": n,
                "p_hit": round(100 * p, 2),
                "p_hit_expected_from_volatility": round(100 * exp, 2),
                "lift_pts": round(100 * (p - exp), 2),
                "z_vs_volatility_only": round((p - exp) / se, 2),
                "mean_next_week_net_pct": round(100 * net, 3),
            }

        early, late = sel[sel["date"] < mid], sel[sel["date"] >= mid]
        full = measure(sel)
        early_metrics, late_metrics = measure(early), measure(late)
        both = (
            early_metrics["lift_pts"] is not None
            and late_metrics["lift_pts"] is not None
            and early_metrics["lift_pts"] > config["robust_lift_minimum_points"]
            and late_metrics["lift_pts"] > config["robust_lift_minimum_points"]
            and full["z_vs_volatility_only"] is not None
            and abs(full["z_vs_volatility_only"]) >= z_needed
        )
        rows.append({
            "pattern": name,
            "full": full,
            "early": early_metrics,
            "late": late_metrics,
            "lift_in_both_halves_and_significant": bool(both),
        })

    rows.sort(
        key=lambda row: (
            row["full"]["lift_pts"] is None,
            -(row["full"]["lift_pts"] or 0),
        )
    )
    result = {
        "question": (
            "Does a chart pattern raise the chance the next week gains >= "
            f"{int(config['hit_return_fraction'] * 100)}%?"
        ),
        "base_rate_pct_all_stock_weeks": round(100 * base, 2),
        "hit_rate_by_volatility_quintile_pct": {
            int(k): round(100 * v, 2) for k, v in stacked.groupby("vol_bucket")["hit"].mean().items()
        },
        "stock_weeks": int(len(stacked)),
        "patterns_tested": n_tests,
        "bonferroni_z_required": round(z_needed, 2),
        "round_trip_cost_pct": round(100 * round_trip_cost, 3),
        "survivorship_note": "universe is today's long-history names; failed companies are absent",
        "results": rows,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=1), encoding="utf8")

    print(f"stock-weeks {len(stacked):,}   base rate P(next week >= {int(config['hit_return_fraction'] * 100)}%) = {result['base_rate_pct_all_stock_weeks']}%")
    print("by volatility quintile (low->high):", result["hit_rate_by_volatility_quintile_pct"])
    print(f"\n{'pattern':<28}{'n':>8}{'P(hit)':>8}{'vol-only':>9}{'lift':>7}{'z':>7}{'early':>7}{'late':>7}{'net%/wk':>9}  robust")
    for r in rows:
        f = r["full"]
        print(
            f"{r['pattern']:<28}{f['n']:>8,}"
            f"{f['p_hit'] if f['p_hit'] is not None else 'N/A':>8}"
            f"{f['p_hit_expected_from_volatility'] if f['p_hit_expected_from_volatility'] is not None else 'N/A':>9}"
            f"{f['lift_pts'] if f['lift_pts'] is not None else 'N/A':>7}"
            f"{f['z_vs_volatility_only'] if f['z_vs_volatility_only'] is not None else 'N/A':>7}"
            f"{r['early']['lift_pts'] if r['early']['lift_pts'] is not None else 'N/A':>7}"
            f"{r['late']['lift_pts'] if r['late']['lift_pts'] is not None else 'N/A':>7}"
            f"{f['mean_next_week_net_pct'] if f['mean_next_week_net_pct'] is not None else 'N/A':>9}"
            f"  {'YES' if r['lift_in_both_halves_and_significant'] else ''}"
        )


if __name__ == "__main__":
    main()
