"""Compare configured delivery and intraday transaction costs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from study_intraweek_target import simulate, weeks_for

from forward.data.cached_market_frames import CachedMarketFramesAdapter
from forward.research.hunt import Costs, stock_universe

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "study_intraday_cost.json"


def costs_at_slippage(config: dict, slippage: float) -> Costs:
    """Build the configured fee model with one explicit slippage scenario.

    Args:
        config: Fee model fields.
        slippage: Per-side slippage fraction for this scenario.

    Returns:
        A complete transaction-cost model.
    """
    return Costs(**{**config, "slippage": slippage})


def main() -> int:
    """Run the configured cost comparison."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    config_path = parser.parse_args().config
    config = json.loads(config_path.read_text(encoding="utf-8"))
    print(f"Configuration: {config_path.resolve()}")
    strategy_path = ROOT / config["base_strategy_config"]
    strategy_config = json.loads(strategy_path.read_text(encoding="utf-8"))
    print(f"Strategy configuration: {strategy_path.resolve()}")

    history = CachedMarketFramesAdapter()
    frames = [
        weeks_for(symbol, history, strategy_config)
        for symbol in stock_universe(
            history,
            min_bars=strategy_config["universe_minimum_bars"],
        )
    ]
    weeks = pd.concat([frame for frame in frames if frame is not None], ignore_index=True)
    weeks["mkt_prior_week"] = weeks.groupby("date")["ret5"].transform("median")
    result = simulate(
        weeks,
        config["stop_fraction"],
        strategy_config["target_fraction"],
        strategy_config,
    )
    split_index = int(len(weeks) * strategy_config["split_fraction"])
    midpoint = weeks["date"].sort_values().iloc[split_index]
    early = (weeks["date"] < midpoint).to_numpy()
    same_day_win = (
        (result["reason"] == "target") & (result["day"] == 0)
    ).to_numpy()
    delivery_costs = Costs(**strategy_config["costs"])
    slippage_cases = config["slippage_scenarios"]
    print(
        f"Delivery round trip {100 * delivery_costs.round_trip_pct(config['delivery_reference_notional']):.3f}%\n"
    )

    gap = weeks["gap_open"]
    conditions = config["conditions"]
    rules = {
        "every stock, every Monday": pd.Series(True, index=weeks.index),
        "gapped down one percent Monday": gap < conditions["gap_down_one_pct"],
        "gapped down two percent Monday": gap < conditions["gap_down_two_pct"],
        "gap down and market rose": (
            (gap < conditions["gap_down_one_pct"])
            & (weeks["mkt_prior_week"] > conditions["market_return_up_pct"])
        ),
        "gap down and above trend": (
            (gap < conditions["gap_down_one_pct"])
            & (weeks["dist_ema50"] > 0)
        ),
    }
    print(
        f"{'rule':<42}{'trades':>8}{'same-day':>10}{'wins':>7}"
        f"{'delivery avg':>14}{'hybrid avg low':>18}{'high':>9}{'late half':>13}"
    )
    gross_return = result["ret"].to_numpy()
    target_win = (result["reason"] == "target").to_numpy()
    scenario_returns: dict[float, np.ndarray] = {}
    for slippage in slippage_cases:
        day_cost = costs_at_slippage(config["day_trade_costs"], slippage)
        delivery_scenario = costs_at_slippage(strategy_config["costs"], slippage)
        same_day_cost = day_cost.round_trip_pct(config["day_trade_reference_notional"]) / 100
        held_cost = delivery_scenario.round_trip_pct(config["delivery_reference_notional"]) / 100
        scenario_returns[slippage] = gross_return - np.where(
            same_day_win,
            same_day_cost,
            held_cost,
        )

    for name, condition in rules.items():
        selected = condition.to_numpy()
        count = int(selected.sum())
        if count < config["minimum_condition_samples"]:
            continue
        same_day_rate = 100 * same_day_win[selected].mean()
        target_rate = 100 * target_win[selected].mean()
        delivery_average = 100 * (
            gross_return[selected]
            - delivery_costs.round_trip_pct(config["delivery_reference_notional"]) / 100
        ).mean()
        low_slippage, high_slippage = slippage_cases
        low_result = scenario_returns[low_slippage]
        high_result = scenario_returns[high_slippage]
        late = selected & ~early
        print(
            f"{name:<42}{count:>8,}{same_day_rate:>9.1f}%{target_rate:>6.0f}%"
            f"{delivery_average:>+13.2f}%{100 * low_result[selected].mean():>+17.2f}%"
            f"{100 * high_result[selected].mean():>+8.2f}%"
            f"{100 * low_result[late].mean():>+12.2f}%"
        )

    print("\nSame-day share of the gap-down winners, by history half:")
    selected = (gap < conditions["gap_down_one_pct"]).to_numpy()
    for label, half in (("early", early), ("late", ~early)):
        winners = selected & half & target_win
        winner_count = int(winners.sum())
        if winner_count == 0:
            print(f"  {label}: unavailable; no winning trades")
            continue
        same_day_rate = 100 * (selected & half & same_day_win).sum() / winner_count
        print(f"  {label}: {same_day_rate:.0f}% of winners reached target on entry day")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
