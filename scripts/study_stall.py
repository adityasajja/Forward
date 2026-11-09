"""Measure forward returns after the configured stalled-stock detector."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from forward.insights.stall import StallConfig, stall_frame

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "study_stall.json"


def main() -> int:
    """Run the stall study using its named experiment and feature configs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    config_path = parser.parse_args().config
    config = json.loads(config_path.read_text(encoding="utf-8"))
    feature_path = ROOT / config["feature_config"]
    feature_config_data = json.loads(feature_path.read_text(encoding="utf-8"))
    feature_config = StallConfig(**feature_config_data)
    print(f"Configuration: {config_path.resolve()}")
    print(f"Feature configuration: {feature_path.resolve()}")

    rows = []
    data_directory = ROOT / config["data_directory"]
    for path in sorted(data_directory.glob("*.parquet")):
        frame = pd.read_parquet(path)
        required = {"open", "high", "low", "close", "ts", "volume"}
        if len(frame) < config["minimum_history_bars"] or not required.issubset(frame.columns):
            continue
        features = stall_frame(frame, feature_config)
        close = features["close"]
        for horizon in config["forward_horizons"]:
            features[f"forward_{horizon}"] = (close.shift(-horizon) / close - 1) * 100
        features["symbol"] = path.stem
        rows.append(
            features.dropna(
                subset=[
                    f"forward_{config['forward_horizons'][0]}",
                    "ext50",
                    "ret20",
                    "off_high",
                    "range_ratio",
                ]
            )
        )
    if not rows:
        raise ValueError("No cached symbols meet the configured history requirements.")
    data = pd.concat(rows, ignore_index=True)
    ran_top = (
        (data["ret20"] >= config["recent_run_return_pct"])
        | (data["ext50"] >= config["recent_run_extension_pct"])
    ) & (data["off_high"] >= -config["near_high_distance_pct"])
    groups = {
        "all days": data,
        "ran up and still moving": data[ran_top & ~data["gate"]],
        "gate": data[data["gate"]],
        "flagged": data[data["flag"]],
    }
    first_horizon, second_horizon = config["forward_horizons"]
    print(
        f"{data['symbol'].nunique()} symbols, {len(data):,} stock-days, "
        f"{data['ts'].min():%d %b %Y} to {data['ts'].max():%d %b %Y}\n"
    )
    print(
        f"{'group':30s} {'n':>7s} | {str(first_horizon) + 'd mean':>9s} "
        f"{str(first_horizon) + 'd med':>8s} | {str(second_horizon) + 'd mean':>10s} "
        f"{str(second_horizon) + 'd med':>9s}"
    )
    for name, part in groups.items():
        future = part.dropna(subset=[f"forward_{second_horizon}"])
        print(
            f"{name:30s} {len(part):7,d} | "
            f"{part[f'forward_{first_horizon}'].mean():9.2f} "
            f"{part[f'forward_{first_horizon}'].median():8.2f} | "
            f"{future[f'forward_{second_horizon}'].mean():10.2f} "
            f"{future[f'forward_{second_horizon}'].median():9.2f}"
        )

    flagged = groups["flagged"]
    baseline = groups["all days"]
    first_returns = f"forward_{first_horizon}"
    for threshold in config["low_progress_thresholds_pct"]:
        print(
            f"\nWithin {first_horizon} sessions, gain stayed below {threshold:.0f}%: "
            f"flagged {(flagged[first_returns] < threshold).mean() * 100:.0f}% vs "
            f"all days {(baseline[first_returns] < threshold).mean() * 100:.0f}%"
        )
    drawdown = config["drawdown_threshold_pct"]
    print(
        f"Fell {abs(drawdown):.0f}%+ within {first_horizon} sessions: "
        f"flagged {(flagged[first_returns] <= drawdown).mean() * 100:.0f}% vs "
        f"all days {(baseline[first_returns] <= drawdown).mean() * 100:.0f}%"
    )
    episodes = flagged.sort_values(["symbol", "ts"]).copy()
    episodes["gap"] = (
        episodes.groupby("symbol")["ts"].diff().dt.days.fillna(config["stall_episode_days"] + 1)
    )
    first_flags = episodes[episodes["gap"] > config["stall_episode_days"]]
    print(
        f"\nDistinct episodes: {len(first_flags):,}  "
        f"mean {first_horizon}d {first_flags[first_returns].mean():.2f}%  "
        f"median {first_horizon}d {first_flags[first_returns].median():.2f}%  "
        f"down {(first_flags[first_returns] < 0).mean() * 100:.0f}%"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
