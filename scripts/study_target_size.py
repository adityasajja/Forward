"""Measure configured target and stop outcomes after gap-down entries."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from forward.data.cached_market_frames import CachedMarketFramesAdapter
from forward.research.hunt import Costs, stock_universe
from study_intraweek_target import simulate, weeks_for

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "study_target_size.json"


def net_returns(
    result: pd.DataFrame,
    day_trade_cost: float,
    delivery_cost: float,
) -> np.ndarray:
    """Subtract cost by whether the target was reached on the entry day.

    Args:
        result: Simulated trade outcomes.
        day_trade_cost: Round-trip intraday fee fraction.
        delivery_cost: Round-trip delivery fee fraction.

    Returns:
        Net return for each simulated trade.
    """
    same_day = ((result["reason"] == "target") & (result["day"] == 0)).to_numpy()
    return result["ret"].to_numpy() - np.where(
        same_day,
        day_trade_cost,
        delivery_cost,
    )


def main() -> int:
    """Compare every configured target and stop combination."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    config_path = parser.parse_args().config
    config = json.loads(config_path.read_text(encoding="utf-8"))
    print(f"Configuration: {config_path.resolve()}")
    strategy_path = ROOT / config["base_strategy_config"]
    strategy_config = json.loads(strategy_path.read_text(encoding="utf-8"))
    cost_path = ROOT / config["cost_config"]
    cost_config = json.loads(cost_path.read_text(encoding="utf-8"))
    print(f"Strategy configuration: {strategy_path.resolve()}")
    print(f"Cost configuration: {cost_path.resolve()}")

    history = CachedMarketFramesAdapter()
    frames = [
        weeks_for(symbol, history, strategy_config)
        for symbol in stock_universe(
            history,
            min_bars=strategy_config["universe_minimum_bars"],
        )
    ]
    weeks = pd.concat([frame for frame in frames if frame is not None], ignore_index=True)
    weeks["market_return"] = weeks.groupby("date")["ret5"].transform("median")
    midpoint = weeks["date"].sort_values().iloc[
        int(len(weeks) * strategy_config["split_fraction"])
    ]
    early = (weeks["date"] < midpoint).to_numpy()
    conditions = config["conditions"]
    rules = {
        "gap down and market rose": (
            (weeks["gap_open"] < conditions["gap_down_threshold_fraction"])
            & (weeks["market_return"] > conditions["market_return_up_threshold_fraction"])
        ).to_numpy(),
        "gap down in any market": (
            weeks["gap_open"] < conditions["gap_down_threshold_fraction"]
        ).to_numpy(),
    }

    delivery_model = Costs(**strategy_config["costs"])
    day_trade_model = Costs(**cost_config["day_trade_costs"])
    delivery_cost = delivery_model.round_trip_pct() / 100
    day_trade_cost = (
        Costs(
            **{
                **cost_config["day_trade_costs"],
                "slippage": config["same_day_slippage_fraction"],
            }
        ).round_trip_pct(cost_config["day_trade_reference_notional"])
        / 100
    )
    print(
        f"Delivery round trip {100 * delivery_cost:.3f}%; "
        f"entry-day round trip {100 * day_trade_cost:.3f}%\n"
    )
    print(
        f"{'target':>7}{'stop':>7}{'wins':>12}{'avg net':>12}"
        f"{'early':>12}{'late':>12}{'avg win':>12}{'avg loss':>12}"
    )
    for name, selected in rules.items():
        print(f"\n{name} ({int(selected.sum()):,} trades)")
        for target in config["target_fractions"]:
            for stop in config["stop_fractions"]:
                result = simulate(weeks, stop, target, strategy_config)
                net = net_returns(result, day_trade_cost, delivery_cost)
                won = (result["reason"] == "target").to_numpy()
                selected_net = net[selected]
                early_net = net[selected & early]
                late_net = net[selected & ~early]
                winning_returns = net[selected & won]
                losing_returns = net[selected & ~won]
                stop_label = "none" if stop is None else f"{int(stop * 100)}%"
                mean_or_na = lambda values: (
                    f"{100 * values.mean():+.2f}%" if len(values) else "N/A"
                )
                print(
                    f"{100 * target:>6.1f}%{stop_label:>7}"
                    f"{100 * won[selected].mean():>11.1f}%"
                    f"{mean_or_na(selected_net):>12}"
                    f"{mean_or_na(early_net):>12}{mean_or_na(late_net):>12}"
                    f"{mean_or_na(winning_returns):>12}{mean_or_na(losing_returns):>12}"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
