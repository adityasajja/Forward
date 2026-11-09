"""Which cross-sectional factors actually predict returns on this universe?

A pre-registration script: it tests hypotheses on forward returns and reports
the ones that fail, so the strategy built afterwards is chosen from evidence
rather than from whatever looked good in a chart.

Method
------
Everything is **point-in-time**. At bar ``t`` a factor reads only data up to
``t`` (``.shift(1)`` after any rolling window) and is scored against the return
from ``t`` to ``t+horizon``. No factor ever sees the return it is judged on.

Each factor is judged on three things, all of which must hold:

1. **Spread** — top quintile minus bottom quintile forward return, with the
   sign the hypothesis predicts.
2. **Monotonicity** — quintile means should move in one direction. A U-shape
   with a fat top quintile is not a factor, it is a volatility residual.
3. **Stability** — the spread must hold in both halves of the window. A spread
   that exists only in one half is a regime, not an edge.

Plus a **beta check**: if a factor's returns are explained by rising beta to the
equal-weight market, it is leverage wearing a costume, not selection.

Usage:
    ./.venv/Scripts/python.exe scripts/research_factor_scan.py
"""

# ruff: noqa: I001 — the src/ path shim must run before the Forward imports.
from __future__ import annotations

import sys
import argparse
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "research_factor_scan.json"


def load_panel(config: dict) -> pd.DataFrame:
    """Load the configured price panel."""
    panel_path = ROOT / config["panel_file"]
    frame = pd.read_parquet(panel_path)
    frame["ts"] = pd.to_datetime(frame["ts"])
    close = frame.pivot_table(index="ts", columns="symbol", values="close").sort_index()
    if close.empty:
        raise ValueError(f"Configured price panel is empty: {panel_path}.")
    return close


def long_frame(*series: pd.Series, names: list[str]) -> pd.DataFrame:
    """Stack several wide (date x symbol) frames into one aligned long frame.

    Stacking each Series separately produces MultiIndexes that pandas refuses to
    union; concatenating the *frames* first keeps one index all the way through.
    """
    frames = [s.stack().rename(n) for s, n in zip(series, names, strict=True)]
    return pd.concat(frames, axis=1).dropna()


def quintile_table(long: pd.DataFrame, factor: str, config: dict, target: str = "fwd") -> pd.Series:
    """Mean return per configured factor quantile (Q1 = lowest factor)."""
    ranked = long[factor].rank(method="first")
    buckets = pd.qcut(ranked, config["quantile_count"], labels=False)
    return long.groupby(buckets)[target].mean() * 100.0


def describe(long: pd.DataFrame, factor: str, high_is_good: bool, config: dict) -> dict:
    table = quintile_table(long, factor, config)
    spread = float(table.iloc[-1] - table.iloc[0])
    if not high_is_good:
        spread = -spread
    diffs = np.diff(table.to_numpy())
    if not high_is_good:
        diffs = -diffs
    monotone = bool((diffs >= 0).all() or (diffs <= 0).all())
    # Directional consistency: does the sign of each step match the overall sign?
    wanted = np.sign(spread)
    consistent = int((np.sign(diffs) == wanted).sum())

    half = len(long) // 2
    first, second = long.iloc[:half], long.iloc[half:]
    def half_spread(part: pd.DataFrame) -> float:
        t = quintile_table(part, factor, config)
        s = float(t.iloc[-1] - t.iloc[0])
        return s if high_is_good else -s

    return {
        "quintiles": table,
        "spread_pct": spread,
        "monotone": monotone,
        "steps_consistent": f"{consistent}/{config['quantile_count'] - 1}",
        "spread_h1": half_spread(first),
        "spread_h2": half_spread(second),
    }


def beta_of_top(long: pd.DataFrame, factor: str, config: dict) -> float:
    """Beta of the factor's top quintile to the equal-weight market."""
    ranked = long[factor].rank(method="first")
    top = long[pd.qcut(ranked, config["quantile_count"], labels=False)
               == config["quantile_count"] - 1]
    market = long.groupby(level=0)["fwd"].mean()
    aligned = top.groupby(level=0)["fwd"].mean()
    joined = pd.concat([aligned.rename("top"), market.rename("mkt")], axis=1).dropna()
    if joined["mkt"].var() <= 0:
        return float("nan")
    return float(joined["top"].cov(joined["mkt"]) / joined["mkt"].var())


def main() -> int:
    """Run the configured, pre-registered factor scan."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    config_path = parser.parse_args().config
    config = json.loads(config_path.read_text(encoding="utf-8"))
    print(f"Configuration: {config_path.resolve()}")
    close = load_panel(config)
    returns = close.pct_change()
    horizon = config["forward_horizon_days"]
    fwd = close.pct_change(horizon).shift(-horizon)

    # ---- factors, all lagged so only past information is used --------------
    factors: dict[str, tuple[pd.Series, bool]] = {
        # trailing return over several windows, skipping the last month
        name: (
            (close.shift(config["momentum_skip_days"]) / close.shift(history) - 1), True
        )
        for name, history in config["momentum_factors"].items()
    }
    factors.update({
        # realised volatility, 126d
        config["volatility_factor_name"]: (
            returns.rolling(config["volatility_lookback_days"]).std(), False
        ),
        # distance from the trailing high (a "proximity to highs" factor)
        config["distance_high_factor_name"]: (
            close / close.rolling(config["distance_high_lookback_days"]).max(), True
        ),
        # trend: price relative to its own moving average
        **{
            name: (close / close.rolling(window).mean(), True)
            for name, window in config["moving_average_factors"].items()
        },
    })

    print(f"window : {close.index[0]:%Y-%m-%d} -> {close.index[-1]:%Y-%m-%d}")
    print(f"names  : {close.shape[1]}   horizon: {horizon} sessions\n")

    results: list[dict] = []
    for name, (series, high_is_good) in factors.items():
        long = long_frame(series.shift(1), fwd, names=[name, "fwd"])
        if long.empty:
            continue
        info = describe(long, name, high_is_good, config)
        info["name"] = name
        info["n"] = len(long)
        info["beta"] = beta_of_top(long, name, config)
        results.append(info)

        q = info["quintiles"]
        print(f"{name}")
        print("  quintiles: " + "  ".join(
            f"Q{i + 1} {v:+.2f}%" for i, v in enumerate(q)))
        print(f"  spread(top-bottom) {info['spread_pct']:+.2f}%   "
              f"monotone={info['monotone']}   steps={info['steps_consistent']}   "
              f"beta(top)={info['beta']:.2f}")
        print(f"  halves: {info['spread_h1']:+.2f}% / {info['spread_h2']:+.2f}%"
              f"   n={info['n']:,}")
        print()

    print("=" * 72)
    print(f"survivors — monotone, same sign in both halves, spread > "
          f"{config['minimum_survivor_spread_pct']}%/month:")
    survivors = [
        r for r in results
        if r["monotone"]
        and r["spread_pct"] > config["minimum_survivor_spread_pct"]
        and r["spread_h1"] > 0
        and r["spread_h2"] > 0
    ]
    if not survivors:
        print("  NONE. No factor tested here is both monotone and stable.")
    for r in survivors:
        print(f"  {r['name']:<16} spread {r['spread_pct']:+.2f}%/mo  "
              f"beta {r['beta']:.2f}  halves "
              f"{r['spread_h1']:+.2f}/{r['spread_h2']:+.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
