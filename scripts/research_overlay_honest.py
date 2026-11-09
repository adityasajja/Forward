"""Can any overlay beat buy-and-hold on risk-adjusted terms?

Context for why this is the right question
------------------------------------------
Research on this repo's data established three things:

1. There is no reliable cross-sectional alpha in these 120 names. Momentum's
   spread fell from +1.01% to +0.27%/month once beta was removed; the only
   monotone factor (high realised vol) is leverage -- it wins +4.07%/month in
   up months and loses -2.06% in down months.
2. The liquid-120 universe carries a +356 point survivorship gap versus the
   unbiased long-history set, so its absolute returns are not quotable.
3. Index-level trend filtering and volatility targeting both *reduced* returns
   more than they reduced risk.

That leaves one hypothesis worth testing, and it is the one a professional
would test first: that nothing predicts *return*, but something predicts
*risk*, and risk is controllable. If a simple, pre-specified overlay improves
the return/drawdown ratio out of sample on the unbiased universe, it is worth
having -- not because it makes money, but because it lets you hold the same
asset with less chance of being forced out at the bottom.

The rules are fixed in advance and tested unchanged on every split, so the
result cannot be the product of a search. The decision metric is **Calmar**
(CAGR / max drawdown) because that is the ratio a leveraged or drawdown-averse
investor actually faces, and it is the one thing the earlier overlays failed.

Usage:
    ./.venv/Scripts/python.exe scripts/research_overlay_honest.py
"""

# ruff: noqa: I001
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "research_overlay_honest.json"


def unbiased_panel(config: dict) -> pd.DataFrame:
    """Every cache symbol with a long history — no liquidity selection."""
    series = {}
    cache = ROOT / config["daily_data_directory"]
    paths = sorted(cache.glob("*.parquet"))
    if not paths:
        raise FileNotFoundError(f"No daily parquet files were found in {cache}.")
    for path in paths:
        frame = pd.read_parquet(path, columns=["ts", "close"])
        if len(frame) < config["minimum_history_bars"]:
            continue
        frame = frame.copy()
        frame["ts"] = pd.to_datetime(frame["ts"])
        series[path.stem.upper()] = frame.set_index("ts")["close"]
    if not series:
        raise ValueError("No daily symbols meet the configured history requirement.")
    return pd.DataFrame(series).sort_index()


def metrics(rets: pd.Series, label: str, config: dict) -> dict:
    """Measure returns without replacing missing observations."""
    if rets.empty or not np.isfinite(rets.to_numpy(dtype=float)).all():
        raise ValueError(f"The {label} return sample is empty or non-finite.")
    eq = (1 + rets).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    years = len(rets) / config["trading_days_per_year"]
    if eq.iloc[-1] <= 0:
        raise ValueError(f"The {label} equity path is non-positive.")
    cagr = eq.iloc[-1] ** (1 / years) - 1
    daily_vol = rets.std()
    if daily_vol <= 0:
        raise ValueError(f"The {label} return sample has no measurable volatility.")
    vol = daily_vol * np.sqrt(config["trading_days_per_year"])
    sharpe = rets.mean() / daily_vol * np.sqrt(config["trading_days_per_year"])
    return {
        "label": label,
        "total_pct": (eq.iloc[-1] - 1) * 100,
        "cagr_pct": cagr * 100,
        "vol_pct": vol * 100,
        "sharpe": sharpe,
        "maxdd_pct": dd * 100,
        "calmar": (cagr / abs(dd)) if dd < 0 else float("nan"),
        "exposure_pct": 100.0,
    }


def overlays(ew: pd.Series, config: dict) -> dict[str, pd.Series]:
    """Position weight (0..1) applied to the next day's return.

    Every rule is lagged by one bar. A weight computed from a return that
    includes the day it is applied to is the most common way an overlay
    backtest lies, and it can turn any rule into a winner.
    """
    out = {}
    ones = pd.Series(1.0, index=ew.index)
    definitions = config["overlays"]

    # 1. Vol scaling: hold less when recent vol is high. Target 15% annual.
    vol_scale = definitions["vol_scale"]
    vol20 = ew.rolling(vol_scale["lookback_days"]).std() * np.sqrt(config["trading_days_per_year"])
    raw = (vol_scale["target_annual_volatility"] / vol20).clip(
        upper=vol_scale["maximum_weight"]
    )
    out["vol_scale_20"] = raw.shift(1)

    # 2. Only the vol brake, no leverage up: full size below median vol.
    brake = definitions["vol_brake"]
    med = vol20.rolling(brake["median_lookback_days"]).median()
    known_brake = vol20.notna() & med.notna()
    out["vol_brake"] = (vol20 <= med).astype(float).where(known_brake).shift(1)

    # 3. Trend: hold only above the configured moving average.
    trend = definitions["trend"]
    ma200 = (1 + ew).cumprod().rolling(trend["moving_average_days"]).mean()
    px = (1 + ew).cumprod()
    out["trend_200"] = (px > ma200).astype(float).where(ma200.notna()).shift(1)

    # 4. Drawdown brake: cut to the configured size after the configured decline, restore on a
    #    new high. Asymmetric on purpose -- de-risking fast, re-risking slow.
    eq = (1 + ew).cumprod()
    dd = eq / eq.cummax() - 1
    drawdown = definitions["drawdown_brake"]
    w = pd.Series(
        np.where(
            dd <= -drawdown["trigger_drawdown_fraction"],
            drawdown["reduced_weight"],
            1.0,
        ),
        index=ew.index,
    ).where(dd.notna())
    out["dd_brake"] = w.shift(1)

    # 5. Combination: vol brake AND trend, the two with independent logic.
    combined_known = known_brake & ma200.notna()
    out["vol_brake+trend"] = (
        (vol20 <= med).astype(float) * (px > ma200).astype(float)
    ).where(combined_known).shift(1)

    out["buy_hold"] = ones
    return out


def main() -> int:
    """Compare configured risk overlays with buy and hold."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    config_path = parser.parse_args().config
    config = json.loads(config_path.read_text(encoding="utf-8"))
    print(f"Configuration: {config_path.resolve()}")
    close = unbiased_panel(config)
    rets = close.pct_change()
    ew = rets.mean(axis=1)
    weights = overlays(ew, config)
    print(f"unbiased universe: {close.shape[1]} names, {len(close)} sessions")
    print(f"window: {close.index[0]:%Y-%m-%d} -> {close.index[-1]:%Y-%m-%d}\n")

    midpoint = int(len(close.index) * config["split_fraction"])
    split_indices = {
        "full": close.index,
        "first half": close.index[:midpoint],
        "second half": close.index[midpoint:],
    }

    for split_name, index in split_indices.items():
        split_returns = ew.loc[index]
        complete_return_dates = split_returns.index[split_returns.notna()]
        if complete_return_dates.empty:
            raise ValueError(f"Split {split_name!r} has no complete return observations.")
        print(f"== {split_name} ({index[0]:%Y-%m} -> {index[-1]:%Y-%m}) ==")
        header = (f"{'overlay':<18} {'CAGR':>7} {'vol':>7} {'Sharpe':>7} "
                  f"{'maxDD':>8} {'Calmar':>7} {'expo':>6}")
        print(header)
        print("-" * len(header))
        rows = []
        baseline_calmar: dict[str, float] = {}
        for name, weight in weights.items():
            split_weight = weight.loc[complete_return_dates]
            first_valid = split_weight.first_valid_index()
            if first_valid is None:
                raise ValueError(
                    f"Overlay {name!r} has no known weights in split {split_name!r}."
                )
            selected_index = complete_return_dates[complete_return_dates >= first_valid]
            split_weight = split_weight.loc[selected_index]
            if split_weight.isna().any():
                raise ValueError(
                    f"Overlay {name!r} has missing weights after warmup in {split_name!r}."
                )
            selected_returns = split_returns.loc[selected_index]
            if not np.isfinite(selected_returns.to_numpy(dtype=float)).all():
                raise ValueError(f"Split {split_name!r} has incomplete market returns.")
            w = split_weight
            m = metrics(selected_returns * w, name, config)
            m["exposure_pct"] = w.mean() * 100
            baseline = metrics(selected_returns, "buy_hold", config)
            baseline_calmar[name] = baseline["calmar"]
            rows.append(m)
            print(f"{name:<18} {m['cagr_pct']:>6.1f}% {m['vol_pct']:>6.1f}% "
                  f"{m['sharpe']:>7.2f} {m['maxdd_pct']:>7.1f}% "
                  f"{m['calmar']:>7.2f} {m['exposure_pct']:>5.0f}%")
        winners = [
            row for row in rows
            if row["label"] != "buy_hold"
            and row["calmar"] > baseline_calmar[row["label"]]
        ]
        print(f"\n  beat buy-and-hold on Calmar: "
              f"{', '.join(r['label'] for r in winners) if winners else 'NONE'}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
