"""Inspect one configured strategy-hunt research window."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import strategy_hunt as hunt

from forward.research.hunt import Costs, buy_and_hold, run_weights

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "debug_hunt.json"


def main() -> int:
    """Run the diagnostic with its explicit parameter file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    config_path = parser.parse_args().config
    config = json.loads(config_path.read_text(encoding="utf-8"))
    print(f"Configuration: {config_path.resolve()}")
    symbols = list(dict.fromkeys(
        symbol
        for relative_path in config["universe_files"]
        for symbol in hunt.parse_universe(ROOT / relative_path)
    ))
    costs = Costs(**config["costs"])
    print("Loaded", len(symbols), "symbols")
    panel = hunt.build_universe_panel(
        symbols,
        pd.Timestamp(config["from_date"]),
        pd.Timestamp(config["to_date"]),
        min_bars=config["minimum_bars"],
    )
    print("Panel:", panel.shape)
    split_index = int(len(panel) * config["split_fraction"])
    train_panel = panel.iloc[:split_index]
    print("Train panel:", train_panel.shape)

    benchmark = buy_and_hold(train_panel, panel.columns[0], costs)
    benchmark_metrics = benchmark.metrics()
    print(
        "B&H CAGR:", benchmark_metrics["cagr_pct"],
        "Sharpe:", benchmark_metrics["sharpe"],
        "Weekly:", benchmark_metrics.get("weekly_mean_pct", "N/A"),
    )
    print("B&H metrics keys:", list(benchmark_metrics.keys()))

    signal = hunt.signal_momentum(train_panel, lookback=config["momentum_lookback"])
    inverse_volatility = 1.0 / train_panel.pct_change().rolling(
        config["inverse_volatility_lookback"],
        min_periods=config["inverse_volatility_minimum_periods"],
    ).std()
    inverse_volatility = inverse_volatility.replace([np.inf, -np.inf], np.nan)
    weights = hunt._build_weights(
        train_panel,
        signal,
        inverse_volatility,
        train_panel.index[0],
        train_panel.index[-1],
        config["weighting_lookback"],
        config["weighting_step"],
        config["weighting_mode"],
    )
    print("Weights:", weights.shape, "sum:", weights.sum().sum(), "NaN count:", weights.isna().sum().sum())
    result = run_weights(train_panel, weights, costs)
    metrics = result.metrics()
    print(
        "Strategy CAGR:", metrics["cagr_pct"],
        "Sharpe:", metrics["sharpe"],
        "Weekly:", metrics.get("weekly_mean_pct", "N/A"),
    )
    print("Strategy metrics keys:", list(metrics.keys()))
    print("Returns len:", len(result.returns), "nonzero:", (result.returns != 0).sum())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
