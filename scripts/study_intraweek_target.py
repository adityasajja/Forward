"""Buy at Monday's open, sell the moment it touches +2%, with a stop and a Friday exit.

The earlier studies asked whether a stock *closed* the week 2% higher. The plan being
tested here is different and easier to hit: enter Monday, place a sell at +2%, and take
it whenever the price touches that at any point in the week. The catch is what happens
when it does not: without a stop, a stock that never bounces is simply held, and the
loss is hidden. So every trade also has a stop, and a Friday-close exit.

Fills are modelled on daily bars, pessimistically:
* entry at Monday's open;
* if a later day opens beyond the target or the stop, the fill is that open (a gap);
* if one day's range touches both the stop and the target, the stop is assumed first;
* a round trip costs 0.33% (statutory plus slippage) and is subtracted from every trade.

Run:  ./.venv/Scripts/python.exe scripts/study_intraweek_target.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from forward.data.cached_market_frames import CachedMarketFramesAdapter
from forward.research.data_hygiene import reverting_spike_mask
from forward.research.hunt import Costs, stock_universe

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "study_intraweek_target.json"


def weeks_for(
    symbol: str,
    history: CachedMarketFramesAdapter,
    config: dict,
) -> pd.DataFrame | None:
    """Build weekly trade rows for one symbol.

    Args:
        symbol: Trading symbol to read.
        history: Adapter that reads cached daily market frames.
        config: Explicit history, indicator, and exchange settings.

    Returns:
        One row per usable week, or ``None`` when history is too short.
    """
    df = history.frames(config["exchange"], [symbol])[symbol][
        ["ts", "open", "high", "low", "close"]
    ].dropna()
    df = df[(df["close"] > 0) & (df["open"] > 0)].drop_duplicates("ts").reset_index(drop=True)
    df = df[df["ts"] >= config["history_start_date"]].reset_index(drop=True)
    if len(df) < config["minimum_symbol_history_bars"]:
        return None
    df = df[~reverting_spike_mask(df["close"]).to_numpy()].reset_index(drop=True)
    week = df["ts"].dt.to_period(config["week_period"])
    first = df.index[week != week.shift(1)].to_numpy()
    days = config["trading_days_per_week"]
    first = first[
        (first >= config["indicator_warmup_days"])
        & (first + days <= len(df))
    ]
    open_prices, high_prices, low_prices, c = (
        df[k].to_numpy() for k in ("open", "high", "low", "close")
    )
    idx = first[:, None] + np.arange(days)[None, :]
    close = pd.Series(c)
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / config["rsi_period"]).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / config["rsi_period"]).mean()
    rsi = (100 - 100 / (1 + gain / loss.replace(0, np.nan))).to_numpy()
    momentum_days = config["momentum_days"]
    vol = close.pct_change().rolling(config["volatility_lookback_days"]).std().to_numpy()
    prev = first - 1  # the Friday close before the week: the last thing known at entry
    ema50 = close.ewm(span=config["ema_period"]).mean().to_numpy()
    hi252 = close.rolling(
        config["high_lookback_days"], min_periods=config["high_minimum_periods"]
    ).max().to_numpy()
    return pd.DataFrame(
        {
            "symbol": symbol, "date": df["ts"].to_numpy()[first],
            "entry": open_prices[first],
            **{f"o{k}": open_prices[idx[:, k]] for k in range(days)},
            **{f"h{k}": high_prices[idx[:, k]] for k in range(days)},
            **{f"l{k}": low_prices[idx[:, k]] for k in range(days)},
            "close_end": c[idx[:, days - 1]],
            "rsi": rsi[prev], "vol20": vol[prev],
            "ret5": c[prev] / c[prev - momentum_days[0]] - 1,
            "ret20": c[prev] / c[prev - momentum_days[1]] - 1,
            "dist_ema50": c[prev] / ema50[prev] - 1, "dist_52w_high": c[prev] / hi252[prev] - 1,
            "gap_open": open_prices[first] / c[prev] - 1,
        }
    )


def simulate(
    w: pd.DataFrame,
    stop: float | None,
    target: float,
    config: dict,
) -> pd.DataFrame:
    """Simulate each configured weekly target and stop outcome.

    Args:
        w: Weekly OHLCV and entry-context rows.
        stop: Stop distance as a fraction, or ``None`` when no stop is used.
        target: Target distance as a fraction.
        config: Explicit trading calendar settings.

    Returns:
        A frame containing exit reason, day, and return for each row.
    """
    entry = w["entry"].to_numpy()
    tgt = entry * (1 + target)
    stp = entry * (1 - stop) if stop is not None else np.full(len(w), -np.inf)
    exit_px = np.full(len(w), np.nan)
    reason = np.array([""] * len(w), dtype=object)
    live = np.ones(len(w), bool)
    days = config["trading_days_per_week"]
    day = np.full(len(w), days - 1)
    for k in range(days):
        open_prices = w[f"o{k}"].to_numpy()
        high_prices = w[f"h{k}"].to_numpy()
        low_prices = w[f"l{k}"].to_numpy()
        if k > 0:  # gaps: only possible after the entry day
            gap_up, gap_dn = live & (open_prices >= tgt), live & (open_prices <= stp)
            exit_px[gap_up], reason[gap_up] = open_prices[gap_up], "target"
            exit_px[gap_dn], reason[gap_dn] = open_prices[gap_dn], "stop"
            day[gap_up | gap_dn] = k
            live &= ~(gap_up | gap_dn)
        hit_stop, hit_tgt = live & (low_prices <= stp), live & (high_prices >= tgt)
        stopped = hit_stop  # stop wins a tie: the pessimistic reading of a bar that touched both
        exit_px[stopped], reason[stopped] = stp[stopped], "stop"
        won = hit_tgt & ~stopped
        exit_px[won], reason[won] = tgt[won], "target"
        day[stopped | won] = k
        live &= ~(stopped | won)
    exit_px[live], reason[live] = w["close_end"].to_numpy()[live], "friday"
    out = w[["symbol", "date", "rsi", "vol20", "ret5", "ret20", "dist_ema50", "dist_52w_high", "gap_open"]].copy()
    out["ret"] = exit_px / entry - 1
    out["day"] = day  # 0 = Monday, the day of entry
    out["reason"] = reason
    return out


def report(tag: str, r: pd.DataFrame, config: dict, round_trip_cost: float) -> None:
    n = len(r)
    if n < config["minimum_report_samples"]:
        return
    net = r["ret"] - round_trip_cost
    print(f"{tag:<34}{n:>8,}  target {100 * (r.reason == 'target').mean():>5.1f}%  stop {100 * (r.reason == 'stop').mean():>5.1f}%  "
          f"friday {100 * (r.reason == 'friday').mean():>5.1f}%  winners {100 * (net > 0).mean():>5.1f}%  "
          f"avg net {100 * net.mean():>+6.2f}%  median {100 * net.median():>+6.2f}%")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    config_path = parser.parse_args().config
    config = json.loads(config_path.read_text(encoding="utf-8"))
    print(f"Configuration: {config_path.resolve()}")
    costs = Costs(**config["costs"])
    round_trip_cost = costs.round_trip_pct() / 100
    history = CachedMarketFramesAdapter()
    frames = [
        weeks_for(symbol, history, config)
        for symbol in stock_universe(history, min_bars=config["universe_minimum_bars"])
    ]
    frames = [frame for frame in frames if frame is not None]
    weeks = pd.concat(frames, ignore_index=True)
    print(f"{len(weeks):,} stock-weeks across {weeks['symbol'].nunique()} stocks, {weeks['date'].min().date()} to {weeks['date'].max().date()}\n")
    print(f"{'exit plan (sell at +2%)':<34}{'trades':>8}\n")
    for stop in config["stop_candidates"]:
        result = simulate(weeks, stop, config["target_fraction"], config)
        report(
            "no stop" if stop is None else f"stop at -{int(stop * 100)}%",
            result,
            config,
            round_trip_cost,
        )

    print("\nSame plan (stop -3%) split by how jumpy the stock is (20-day volatility):")
    base_stop = config["stop_candidates"][config["base_stop_candidate_index"]]
    base = simulate(weeks, base_stop, config["target_fraction"], config)
    q = pd.qcut(
        base["vol20"], config["volatility_buckets"],
        labels=config["volatility_bucket_labels"],
    )
    for name in config["volatility_bucket_labels"]:
        report(f"  {name} bucket", base[q == name], config, round_trip_cost)

    print("\nAnd for the configured oversold and high-volatility pattern:")
    cut = base["vol20"].quantile(config["high_volatility_quantile"])
    for stop in config["oversold_stop_candidates"]:
        result = simulate(weeks, stop, config["target_fraction"], config)
        condition = (
            (result["rsi"] < config["oversold_rsi_maximum"])
            & (result["vol20"] >= cut)
        )
        report(
            f"  oversold + volatile, stop -{int(stop * 100)}%",
            result[condition],
            config,
            round_trip_cost,
        )
    mid = weeks["date"].sort_values().iloc[
        int(len(weeks) * config["split_fraction"])
    ]
    b3 = simulate(weeks, base_stop, config["target_fraction"], config)
    print("\nEarly versus late half (stop -3%):")
    report("  all stocks, early half", b3[b3["date"] < mid], config, round_trip_cost)
    report("  all stocks, late half", b3[b3["date"] >= mid], config, round_trip_cost)


if __name__ == "__main__":
    main()
