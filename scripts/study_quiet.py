"""Measure future volatility after configured low-volatility periods."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "study_quiet.json"


def main() -> int:
    """Compare quiet periods with all eligible stock-days."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    config_path = parser.parse_args().config
    config = json.loads(config_path.read_text(encoding="utf-8"))
    print(f"Configuration: {config_path.resolve()}")
    rows = []
    data_directory = ROOT / config["data_directory"]
    for path in sorted(data_directory.glob("*.parquet")):
        frame = pd.read_parquet(
            path,
            columns=["ts", "open", "high", "low", "close"],
        )
        if len(frame) < config["minimum_history_bars"]:
            continue
        frame = frame.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
        close, high, low = frame["close"], frame["high"], frame["low"]
        span = high - low
        quiet_return_days = config["quiet_return_days"]
        ret = (close / close.shift(quiet_return_days) - 1) * 100
        typical_move = ret.abs().rolling(
            config["typical_move_lookback_days"]
        ).mean().shift(quiet_return_days)
        forward_move = (
            (close.shift(-config["forward_days"]) / close - 1) * 100
        ).abs() / typical_move
        quiet = (
            (ret.abs() <= config["quiet_return_maximum_pct"])
            & (
                span.rolling(config["quiet_range_days"]).mean()
                / span.rolling(config["range_baseline_days"]).mean()
                <= config["quiet_range_ratio_maximum"]
            )
        )
        high_distance_pct = (
            close / high.rolling(config["top_lookback_days"]).max() - 1
        ) * 100
        at_top = high_distance_pct >= -config["top_distance_maximum_pct"]
        rows.append(
            pd.DataFrame(
                {
                    "ts": frame["ts"],
                    "forward_move": forward_move,
                    "quiet": quiet,
                    "at_top": at_top,
                }
            ).dropna()
        )
    data = pd.concat(rows, ignore_index=True)
    midpoint = data["ts"].quantile(config["history_split_quantile"])
    print(
        "Next configured-session move as a multiple of the stock's usual move "
        "(1.00 means normal)\n"
    )
    print(f"{'':32s} | {'early n':>8s} {'x usual':>8s} | {'late n':>8s} {'x usual':>8s}")
    selections = (
        ("all days", data["forward_move"].notna()),
        ("went quiet", data["quiet"]),
        ("went quiet near the top", data["quiet"] & data["at_top"]),
    )
    for label, selected in selections:
        early = data[selected & (data["ts"] <= midpoint)]
        late = data[selected & (data["ts"] > midpoint)]
        print(
            f"{label:32s} | {len(early):8,d} {early['forward_move'].mean():8.2f} | "
            f"{len(late):8,d} {late['forward_move'].mean():8.2f}"
        )
    quiet_at_top = data[data["quiet"] & data["at_top"]]
    under_half_fraction = (
        quiet_at_top["forward_move"] < config["half_usual_move_fraction"]
    ).mean()
    all_under_half_fraction = (
        data["forward_move"] < config["half_usual_move_fraction"]
    ).mean()
    print(
        f"\nAfter going quiet near the top, the next move was under "
        f"{config['half_usual_move_fraction']:.0%} usual size "
        f"{100 * under_half_fraction:.0f}% of the time; all days: "
        f"{100 * all_under_half_fraction:.0f}%."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
