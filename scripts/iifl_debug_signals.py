"""Inspect configured daily signal counts from IIFL minute data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "iifl_debug_signals.json"


def main() -> int:
    """Build daily panels and report configured signal counts."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    config_path = parser.parse_args().config
    config = json.loads(config_path.read_text(encoding="utf-8"))
    print(f"Configuration: {config_path.resolve()}")

    cache = ROOT / config["cache_directory"]
    data = {
        symbol: pd.read_parquet(cache / f"{symbol}.parquet")
        for symbol in config["symbols"]
    }
    print(f"Symbols loaded: {list(data)}")
    print(f"Total bars: {sum(len(frame) for frame in data.values())}")

    daily: dict[str, pd.DataFrame] = {}
    for symbol, frame in data.items():
        bars = frame.copy()
        bars["date"] = bars["ts"].dt.normalize()
        daily[symbol] = bars.groupby("date").agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
        )
        summary = daily[symbol]
        print(
            f"{symbol}: {len(summary)} daily bars, "
            f"{summary.index[0]} to {summary.index[-1]}"
        )

    all_dates = sorted({stamp for frame in daily.values() for stamp in frame.index})
    print(f"Total daily dates: {len(all_dates)}")
    close_daily = pd.DataFrame(
        {symbol: frame["close"].reindex(all_dates) for symbol, frame in daily.items()}
    ).ffill()
    open_daily = pd.DataFrame(
        {symbol: frame["open"].reindex(all_dates) for symbol, frame in daily.items()}
    ).ffill()
    volume_daily = pd.DataFrame(
        {symbol: frame["volume"].reindex(all_dates) for symbol, frame in daily.items()}
    ).ffill()
    print(f"\nPanel shape: {close_daily.shape}")
    print(f"Date range: {all_dates[0]} to {all_dates[-1]}")

    gap = open_daily / close_daily.shift(1) - 1.0
    print(f"\nGap stats: mean={gap.mean().mean():.4f}, max={gap.max().max():.4f}")
    volume_average = volume_daily.rolling(config["volume_lookback"]).mean()
    volume_ratio = volume_daily / volume_average.replace(0, np.nan)
    print(
        f"Volume ratio stats: mean={volume_ratio.mean().mean():.2f}, "
        f"median={volume_ratio.median().median():.2f}"
    )
    momentum = close_daily / close_daily.shift(config["momentum_lookback"]) - 1.0
    print(f"Momentum stats: mean={momentum.mean().mean():.4f}")

    index_close = close_daily[config["index_symbol"]]
    index_sma = index_close.rolling(config["regime_lookback"]).mean()
    regime_ok = index_close > index_sma
    print(
        f"Regime: {regime_ok.sum()}/{len(regime_ok)} sessions bullish "
        f"({100 * regime_ok.mean():.0f}%)"
    )

    momentum_minimum = config["minimum_momentum_pct"] / 100.0
    signal_mask = (
        (gap >= config["gap_threshold_pct"] / 100.0)
        & (volume_ratio >= config["volume_ratio_threshold"])
        & (momentum > momentum_minimum)
    )
    print(
        "\nConfigured signal count: "
        f"{signal_mask.sum().sum()} (gap >= {config['gap_threshold_pct']}%, "
        f"volume ratio >= {config['volume_ratio_threshold']}, "
        f"momentum > {config['minimum_momentum_pct']}%)"
    )

    no_gap_mask = (gap >= config["no_gap_threshold_pct"] / 100.0) & (
        momentum > momentum_minimum
    )
    print(f"No-gap comparison count: {no_gap_mask.sum().sum()}")

    for symbol in close_daily.columns:
        if symbol == config["index_symbol"]:
            continue
        symbol_gap = (gap[symbol] >= config["gap_threshold_pct"] / 100.0).sum()
        symbol_volume = (volume_ratio[symbol] >= config["volume_ratio_threshold"]).sum()
        symbol_momentum = (momentum[symbol] > momentum_minimum).sum()
        symbol_signals = signal_mask[symbol].sum()
        print(
            f"  {symbol}: gap={symbol_gap}, volume={symbol_volume}, "
            f"momentum={symbol_momentum}, all={symbol_signals}"
        )

    print(
        f"\nSample gap-up sessions (first {config['sample_warmup_sessions']} excluded):"
    )
    for index in range(config["sample_warmup_sessions"], len(all_dates)):
        day_gaps = gap.iloc[index]
        large_gaps = day_gaps[
            day_gaps > config["sample_gap_threshold_pct"] / 100.0
        ].sort_values(ascending=False)
        if not large_gaps.empty:
            print(
                f"  {all_dates[index].date()}: "
                f"{large_gaps.head(config['sample_rows']).to_dict()}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
