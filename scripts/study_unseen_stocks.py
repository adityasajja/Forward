"""Measure the configured signal on symbols excluded from earlier research."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from forward.data.cached_market_frames import CachedMarketFramesAdapter
from forward.research.forward_tracker import _rsi
from forward.research.hunt import Costs, stock_universe

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "study_unseen_stocks.json"


def main() -> int:
    """Run the holdout study with explicit configuration."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    config_path = parser.parse_args().config
    config = json.loads(config_path.read_text(encoding="utf-8"))
    print(f"Configuration: {config_path.resolve()}")
    history = CachedMarketFramesAdapter()
    seen = set(stock_universe(history, min_bars=config["minimum_universe_history_bars"]))
    close_cols, value_cols = [], []
    frames = history.frames(config["exchange"])
    for symbol, frame in frames.items():
        if not symbol.endswith(config["symbol_suffix"]):
            continue
        base = symbol.removesuffix(config["symbol_suffix"])
        if base in seen:
            continue
        df = frame[["ts", "close", "volume"]].dropna()
        df = df[df["close"] > 0].drop_duplicates("ts")
        if len(df) < config["minimum_symbol_history_bars"]:
            continue
        df = df.set_index("ts")
        close_cols.append(df["close"].rename(base))
        value_cols.append((df["close"] * df["volume"]).rename(base))
    if not close_cols:
        raise ValueError("No unseen symbols meet the configured history requirement.")
    close = pd.concat(close_cols, axis=1, sort=True)
    value = pd.concat(value_cols, axis=1, sort=True)
    print(
        f"{close.shape[1]} unseen symbols, {len(close)} sessions "
        f"({close.index[0].date()} to {close.index[-1].date()})"
    )

    returns = close.pct_change()
    rsi = close.apply(lambda prices: _rsi(prices, config["rsi_period"]))
    volatility = returns.rolling(config["volatility_lookback_days"]).std()
    liquid = value.rolling(config["liquidity_lookback_days"]).median() >= (
        config["liquid_value_threshold_crore"] * config["crore_in_rupees"]
    )
    forward_return = close.shift(-config["forward_days"]) / close - 1
    weeks = list(close.groupby(close.index.to_period(config["week_period"])).tail(1).index)
    rows = []
    for date in weeks:
        vol, strength, outcome, liquidity = (
            volatility.loc[date],
            rsi.loc[date],
            forward_return.loc[date],
            liquid.loc[date],
        )
        valid = vol.notna() & strength.notna() & outcome.notna()
        if valid.sum() < config["minimum_weekly_cross_section"]:
            continue
        cutoff = vol[valid].quantile(config["volatility_quantile"])
        selected = valid & (strength < config["rsi_maximum"]) & (vol >= cutoff)
        for symbol in selected[selected].index:
            rows.append(
                {
                    "date": date,
                    "symbol": symbol,
                    "return": outcome[symbol],
                    "hit": float(outcome[symbol] >= config["hit_return_fraction"]),
                    "liquid": bool(liquidity[symbol]),
                }
            )
        rows.append(
            {
                "date": date,
                "symbol": config["baseline_symbol"],
                "return": float(outcome[valid].mean()),
                "hit": float((outcome[valid] >= config["hit_return_fraction"]).mean()),
                "liquid": True,
            }
        )
    frame = pd.DataFrame(rows)
    baseline = frame[frame["symbol"] == config["baseline_symbol"]]["hit"].mean()
    signals = frame[frame["symbol"] != config["baseline_symbol"]]
    print(f"Ordinary stock-week rate in this universe: {100 * baseline:.1f}%\n")
    costs = Costs(**config["costs"])
    cost_fraction = costs.round_trip_pct() / 100

    def show(label: str, part: pd.DataFrame) -> None:
        """Print the configured hit-rate and return summary."""
        if len(part) < config["minimum_signal_count"]:
            print(f"{label:<26} only {len(part)} signals")
            return
        count = len(part)
        hits = float(part["hit"].sum())
        probability = hits / count
        z = config["confidence_z"]
        centre = (probability + z * z / (2 * count)) / (1 + z * z / count)
        half = (
            z
            * np.sqrt(probability * (1 - probability) / count + z * z / (4 * count * count))
            / (1 + z * z / count)
        )
        weekly = part.groupby("date")["hit"].mean()
        print(
            f"{label:<26} n={count:>5} hit {100 * probability:>5.1f}% "
            f"(likely {100 * (centre - half):.0f}-{100 * (centre + half):.0f}%) "
            f"weeks {len(weekly):>3} week-weighted {100 * weekly.mean():.1f}% "
            f"net avg {100 * (part['return'].mean() - cost_fraction):+.2f}%/wk "
            f"median {100 * part['return'].median():+.2f}%"
        )

    show("all unseen symbols", signals)
    show("liquid symbols", signals[signals["liquid"]])
    show("illiquid symbols", signals[~signals["liquid"]])
    busiest = signals.groupby("date").size().sort_values(ascending=False)
    examples = config["busy_week_examples"]
    print(
        f"\nBusiest weeks hold {100 * busiest.head(examples).sum() / busiest.sum():.0f}% "
        f"of signals: "
        + ", ".join(f"{date.date()} n={count}" for date, count in busiest.head(examples).items())
    )
    excluded = signals[~signals["date"].isin(busiest.head(examples).index)]
    show("excluding busiest weeks", excluded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
