"""Search configured Monday entry conditions on separate history halves."""

from __future__ import annotations

import argparse
import json
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd
from study_intraweek_target import simulate, weeks_for

from forward.data.cached_market_frames import CachedMarketFramesAdapter
from forward.research.hunt import Costs, stock_universe

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "study_intraweek_search.json"


def main() -> int:
    """Rank conditions on early history and score them on later history."""
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
    costs = Costs(**strategy_config["costs"])
    net = (result["ret"] - costs.round_trip_pct() / 100).to_numpy()
    won = (result["reason"] == "target").to_numpy()
    midpoint = weeks["date"].sort_values().iloc[
        int(len(weeks) * strategy_config["split_fraction"])
    ]
    early = (weeks["date"] < midpoint).to_numpy()
    late = ~early
    thresholds = config["conditions"]
    volatility_quantiles = config["volatility_quantiles"]
    volatility_cutoffs = {
        label: float(weeks["vol20"].quantile(quantile))
        for label, quantile in volatility_quantiles.items()
    }
    rsi_low, rsi_high = thresholds["rsi_mid_range"]
    flags = {
        "fell_last_week": weeks["ret5"] < thresholds["weekly_return_down_fraction"],
        "rose_last_week": weeks["ret5"] > thresholds["weekly_return_up_fraction"],
        "down_month": weeks["ret20"] < thresholds["monthly_return_down_fraction"],
        "up_month": weeks["ret20"] > thresholds["monthly_return_up_fraction"],
        "rsi_oversold": weeks["rsi"] < thresholds["rsi_oversold"],
        "rsi_overbought": weeks["rsi"] > thresholds["rsi_overbought"],
        "rsi_mid": weeks["rsi"].between(rsi_low, rsi_high),
        "jumpy": weeks["vol20"] >= volatility_cutoffs["jumpy"],
        "calm": weeks["vol20"] <= volatility_cutoffs["calm"],
        "above_ema50": weeks["dist_ema50"] > thresholds["ema50_above_fraction"],
        "below_ema50": weeks["dist_ema50"] < thresholds["ema50_below_fraction"],
        "near_52w_high": weeks["dist_52w_high"] > thresholds["near_52_week_high_fraction"],
        "far_from_high": weeks["dist_52w_high"] < thresholds["far_from_52_week_high_fraction"],
        "gapped_up_monday": weeks["gap_open"] > thresholds["gap_up_fraction"],
        "gapped_down_monday": weeks["gap_open"] < thresholds["gap_down_fraction"],
        "market_fell_last_week": weeks["mkt_prior_week"] < thresholds["market_return_down_fraction"],
        "market_rose_last_week": weeks["mkt_prior_week"] > thresholds["market_return_up_fraction"],
    }
    names = list(flags)
    matrix = np.column_stack([flags[name].to_numpy() for name in names])
    target_pct = int(strategy_config["target_fraction"] * 100)
    stop_pct = int(config["stop_fraction"] * 100)
    print(
        f"{len(weeks):,} stock-weeks. Plan: buy Monday open, target +{target_pct}%, "
        f"stop -{stop_pct}%, otherwise exit at week close."
    )
    print(
        f"Overall: win {100 * won.mean():.1f}%   avg net {100 * net.mean():+.2f}% "
        f"(early {100 * net[early].mean():+.2f}%, late {100 * net[late].mean():+.2f}%)\n"
    )

    tried = []
    for size in config["condition_sizes"]:
        for combo in combinations(range(len(names)), size):
            mask = matrix[:, combo].all(axis=1)
            training_mask = mask & early
            sample_count = int(training_mask.sum())
            if sample_count >= config["minimum_training_samples"]:
                tried.append((float(net[training_mask].mean()), combo, mask))
    tried.sort(key=lambda item: -item[0])
    minimum_training_samples = config["minimum_training_samples"]
    print(
        f"{len(tried)} conditions with at least {minimum_training_samples:,} "
        "early-half trades. Best by early average, scored on late history:\n"
    )
    print(
        f"{'condition (all must hold)':<62}{'early avg':>10}"
        f"{'late avg':>10}{'late n':>9}{'late win':>9}"
    )
    for average, combo, mask in tried[:config["top_conditions"]]:
        late_mask = mask & late
        print(
            f"{' + '.join(names[index] for index in combo):<62}"
            f"{100 * average:>+9.2f}%{100 * net[late_mask].mean():>+9.2f}%"
            f"{int(late_mask.sum()):>9,}{100 * won[late_mask].mean():>8.1f}%"
        )

    rng = np.random.default_rng(config["random_seed"])
    best_noise = []
    for _ in range(config["randomized_search_trials"]):
        shuffled = rng.permutation(net[early])
        best_noise.append(
            max(float(shuffled[mask[early]].mean()) for _, _, mask in tried)
        )
    print(
        "\nBest early average on shuffled outcomes: "
        f"{100 * np.mean(best_noise):+.2f}%"
    )
    top_conditions = tried[:config["top_conditions"]]
    positive = [
        (average, combo, mask)
        for average, combo, mask in top_conditions
        if net[mask & late].mean() > config["minimum_late_average_fraction"]
    ]
    print(
        f"Of the top {len(top_conditions)} early-selected conditions, "
        f"{len(positive)} also pass the configured late-period average."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
