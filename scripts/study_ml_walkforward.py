"""Let a model search for the pattern, and grade it only on years it never saw.

The hand-picked patterns tried so far are a tiny corner of what could be tested. A
gradient-boosted model can weigh dozens of measurements at once and find interactions
nobody would think to write down. The danger is the opposite one: with that much
freedom it will happily memorise the past. So the rules of the test are strict:

* **Walk-forward.** For each test year, train only on the years before it. The model
  never sees a row from the year it is scored on, and is retrained the next year.
* **Calibration is the real question.** The user wants a rule that is right 60% of the
  time. So the test is whether, when the model says "60%", things actually happen 60%
  of the time, not whether it ranks stocks well.
* **A week is one observation.** Hundreds of stocks share a week's market move, so the
  top few picks per week are scored, and reported per week, not per stock-row.

Entry is modelled at the signal close, which is slightly generous (a real order fills
the next session). The stock universe is today's long-history names, so failed
companies are absent and oversold-bounce style results are flattered.

Run:  ./.venv/Scripts/python.exe scripts/study_ml_walkforward.py
"""

from __future__ import annotations

import argparse
import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score

from forward.data.cached_market_frames import CachedMarketFramesAdapter
from forward.research.data_hygiene import reverting_spike_mask
from forward.research.hunt import Costs, stock_universe

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "study_ml_walkforward.json"


def load(history: CachedMarketFramesAdapter, config: dict) -> dict[str, pd.DataFrame]:
    """Load clean close, high, low, and volume panels for the stock universe.

    Args:
        history: Adapter that reads cached daily market frames.

    Returns:
        Panels keyed by field name.
    """
    names = stock_universe(history, min_bars=config["minimum_universe_history_bars"])
    cached = history.frames(config["exchange"], names)
    out: dict[str, list[pd.Series]] = {k: [] for k in ("close", "high", "low", "volume")}
    for s in names:
        df = cached[s][["ts", "close", "high", "low", "volume"]]
        df = (
            df[(df["close"] > 0) & (df["volume"] >= 0)]
            .drop_duplicates("ts")
            .set_index("ts")
            .loc[config["history_start_date"] :]
        )
        bad = reverting_spike_mask(df["close"]).to_numpy()
        df.loc[df.index[bad], "close"] = np.nan
        for k in out:
            out[k].append(df[k].rename(s))
    return {k: pd.concat(v, axis=1, sort=True) for k, v in out.items()}


def feature_frames(d: dict[str, pd.DataFrame], config: dict) -> dict[str, pd.DataFrame]:
    """Numeric measurements per stock per day, each using only data up to that close."""
    c, h, low, v = d["close"], d["high"], d["low"], d["volume"]
    ret = c.pct_change()
    f: dict[str, pd.DataFrame] = {}
    for n in config["feature_return_periods"]:
        f[f"ret_{n}d"] = c / c.shift(n) - 1
    for n in config["volatility_periods"]:
        f[f"vol_{n}d"] = ret.rolling(n).std()
    short_volatility, _, long_volatility = config["volatility_periods"]
    f[f"vol_ratio_{short_volatility}_{long_volatility}"] = (
        f[f"vol_{short_volatility}d"] / f[f"vol_{long_volatility}d"]
    )
    delta = c.diff()
    gain, loss = (
        delta.clip(lower=0).ewm(alpha=1 / config["rsi_period"]).mean(),
        (-delta.clip(upper=0)).ewm(alpha=1 / config["rsi_period"]).mean(),
    )
    f[f"rsi{config['rsi_period']}"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    for n in config["ema_distance_periods"]:
        f[f"dist_ema{n}"] = c / c.ewm(span=n).mean() - 1
    f["dist_52w_high"] = c / c.rolling(config["yearly_high_period"]).max() - 1
    f["dist_52w_low"] = c / c.rolling(config["yearly_high_period"]).min() - 1
    f[f"dist_{config['recent_high_period']}d_high"] = (
        c / c.rolling(config["recent_high_period"]).max() - 1
    )
    f[f"range_{config['range_period']}d"] = (
        h.rolling(config["range_period"]).max() - low.rolling(config["range_period"]).min()
    ) / c
    f["today_range"] = (h - low) / c
    volume_short, volume_long = config["volume_ratio_periods"]
    f[f"vol_ratio_{volume_short}_{volume_long}"] = (
        v.rolling(volume_short).mean() / v.rolling(volume_long).mean()
    )
    f[f"vol_today_vs_{volume_long}"] = v / v.rolling(volume_long).mean()
    f[f"gap_max_{config['max_gap_drop_period']}d"] = ret.rolling(
        config["max_gap_drop_period"]
    ).max()
    f[f"drop_max_{config['max_gap_drop_period']}d"] = ret.rolling(
        config["max_gap_drop_period"]
    ).min()
    f[f"up_days_{config['up_days_period']}"] = (ret > 0).rolling(config["up_days_period"]).mean()
    # Market context: the same measurement on the average stock. A stock's week is mostly
    # its market's week, so the model needs to know what the market is doing.
    mkt = ret.mean(axis=1)
    mkt_level = (1 + mkt.fillna(0)).cumprod()
    for n in config["market_return_periods"]:
        name = f"mkt_ret_{n}d"
        f[name] = pd.DataFrame(
            np.repeat((mkt_level / mkt_level.shift(n) - 1).to_numpy()[:, None], c.shape[1], 1),
            index=c.index,
            columns=c.columns,
        )
    mkt_vol_period = config["market_volatility_period"]
    f[f"mkt_vol_{mkt_vol_period}d"] = pd.DataFrame(
        np.repeat(mkt.rolling(mkt_vol_period).std().to_numpy()[:, None], c.shape[1], 1),
        index=c.index,
        columns=c.columns,
    )
    mkt_trend_period = config["market_trend_period"]
    f[f"mkt_above_{mkt_trend_period}d"] = pd.DataFrame(
        np.repeat(
            (mkt_level > mkt_level.rolling(mkt_trend_period).mean())
            .astype(float)
            .to_numpy()[:, None],
            c.shape[1],
            1,
        ),
        index=c.index,
        columns=c.columns,
    )
    # Where the stock ranks against its peers today: relative strength is the classic pattern.
    f["rank_ret_20d"] = f["ret_20d"].rank(axis=1, pct=True)
    f["rank_ret_5d"] = f["ret_5d"].rank(axis=1, pct=True)
    f["rank_vol_20d"] = f["vol_20d"].rank(axis=1, pct=True)
    return f


def dataset(config: dict) -> tuple[pd.DataFrame, list[str]]:
    d = load(CachedMarketFramesAdapter(), config)
    feats = feature_frames(d, config)
    close = d["close"]
    weeks = list(close.groupby(close.index.to_period(config["week_period"])).tail(1).index)
    fwd = (close.shift(-config["forward_days"]) / close - 1).loc[weeks]
    stacked = {"fwd": fwd.stack()}
    for name, frame in feats.items():
        stacked[name] = frame.loc[weeks].stack()
    df = pd.DataFrame(stacked).replace([np.inf, -np.inf], np.nan).dropna(subset=["fwd"])
    df = df.dropna(thresh=int(config["feature_non_null_fraction"] * df.shape[1]))
    df["hit"] = (df["fwd"] >= config["hit_return_fraction"]).astype(int)
    df["date"] = df.index.get_level_values(0)
    df["year"] = df["date"].dt.year
    return df, list(feats)


def main() -> int:
    """Train and score configured expanding-window models."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    config_path = parser.parse_args().config
    config = json.loads(config_path.read_text(encoding="utf-8"))
    print(f"Configuration: {config_path.resolve()}")
    costs = Costs(**config["stock_costs"])
    round_trip = costs.round_trip_pct() / 100
    output_path = ROOT / config["output_file"]
    warnings.filterwarnings(config["warning_filter"])
    df, cols = dataset(config)
    print(
        f"{len(df):,} stock-weeks, {len(cols)} measurements, {df['date'].nunique()} weeks, base rate {100 * df['hit'].mean():.1f}%\n"
    )

    scored = []
    first_probability, second_probability = config["probability_thresholds"]
    print(
        f"{'test year':<10}{'base':>7}{'AUC':>7}"
        f"{'top-' + str(config['top_picks_per_week']) + '/wk P':>12}"
        f"{f'P(model>={first_probability})':>14}{'n':>6}"
        f"{f'P(model>={second_probability})':>14}{'n':>6}"
    )
    for year in range(config["first_test_year"], int(df["year"].max()) + 1):
        train, test = df[df["year"] < year], df[df["year"] == year].copy()
        if len(train) < config["minimum_training_rows"] or len(test) < config["minimum_test_rows"]:
            continue
        model = HistGradientBoostingClassifier(
            max_depth=config["model"]["max_depth"],
            learning_rate=config["model"]["learning_rate"],
            max_iter=config["model"]["max_iter"],
            min_samples_leaf=config["model"]["minimum_samples_leaf"],
            l2_regularization=config["model"]["l2_regularization"],
            random_state=config["model"]["random_state"],
        )
        model.fit(train[cols], train["hit"])
        test["p"] = model.predict_proba(test[cols])[:, 1]
        scored.append(test)
        auc = roc_auc_score(test["hit"], test["p"])
        top3 = (
            test.sort_values("p", ascending=False)
            .groupby("date")
            .head(config["top_picks_per_week"])
        )
        hi5, hi6 = test[test["p"] >= first_probability], test[test["p"] >= second_probability]

        def cell(values: pd.DataFrame) -> str:
            """Format one model probability bucket."""
            if len(values) < config["minimum_probability_bin_samples"]:
                return f"{'--':>14}"
            return f"{100 * values['hit'].mean():>13.1f}%"

        print(
            f"{year:<10}{100 * test['hit'].mean():>6.1f}%{auc:>7.3f}{100 * top3['hit'].mean():>11.1f}%{cell(hi5)}{len(hi5):>6}{cell(hi6)}{len(hi6):>6}"
        )

    if not scored:
        raise ValueError("No test years meet the configured training and test sample sizes.")
    all_test = pd.concat(scored)
    print(
        "\nCALIBRATION over every out-of-sample year pooled: when the model says X, how often does it happen?"
    )
    print(f"{'model says':<14}{'actually':>10}{'n':>9}{'net avg %/wk':>15}")
    bins = config["calibration_bins"]
    all_test["bin"] = pd.cut(all_test["p"], bins, right=False)
    rows = []
    for b, g in all_test.groupby("bin", observed=True):
        net = 100 * (g["fwd"].mean() - round_trip)
        print(f"{str(b):<14}{100 * g['hit'].mean():>9.1f}%{len(g):>9,}{net:>15.3f}")
        rows.append(
            {
                "bin": str(b),
                "actual_p_hit": round(100 * g["hit"].mean(), 2),
                "n": int(len(g)),
                "net_pct_per_week": round(net, 3),
            }
        )

    top3 = (
        all_test.sort_values("p", ascending=False)
        .groupby("date")
        .head(config["top_picks_per_week"])
    )
    by_week = top3.groupby("date").agg(hit=("hit", "mean"), ret=("fwd", "mean"))
    print(
        f"\nTop {config['top_picks_per_week']} picks each week, {len(by_week)} out-of-sample weeks:"
    )
    print(
        f"   P(a pick gains >= {config['hit_return_fraction']:.0%}) {100 * top3['hit'].mean():.1f}%"
    )
    print(
        f"   weeks where the basket gained >= {config['hit_return_fraction']:.0%}:  {100 * (by_week['ret'] >= config['hit_return_fraction']).mean():.1f}%"
    )
    print(
        f"   basket average net per week: {100 * (by_week['ret'].mean() - round_trip):+.3f}%   median {100 * by_week['ret'].median():+.3f}%"
    )
    print(f"   base rate for comparison:     {100 * all_test['hit'].mean():.1f}%")

    # Is the top-3 result broad, or carried by a few great weeks or one lucky year?
    by_week["year"] = by_week.index.year
    print(f"\nTop-{config['top_picks_per_week']} basket by year (net of costs):")
    for y, g in by_week.groupby("year"):
        print(
            f"   {y}: {len(g):>3} weeks   avg {100 * (g['ret'].mean() - round_trip):+6.2f}%/wk   median {100 * (g['ret'].median() - round_trip):+6.2f}%   weeks>=target: {100 * (g['ret'] >= config['hit_return_fraction']).mean():>4.0f}%"
        )
    ordered = by_week["ret"].sort_values()
    trimmed = ordered.iloc[: int(len(ordered) * config["trim_best_week_fraction"])]
    print(
        f"   dropping the best configured share of weeks: avg {100 * (trimmed.mean() - round_trip):+.3f}%/wk   worst week {100 * ordered.iloc[0]:+.1f}%   best {100 * ordered.iloc[-1]:+.1f}%"
    )
    curve = (1 + by_week["ret"] - round_trip).cumprod()
    print(
        f"   compounded over the {len(by_week)} weeks: x{curve.iloc[-1]:.1f}   max drawdown {100 * float((curve / curve.cummax() - 1).min()):.0f}%"
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            {
                "method": "gradient-boosted classifier, expanding-window walk-forward, one retrain per test year",
                "measurements": cols,
                "calibration_pooled_out_of_sample": rows,
                "top3_per_week": {
                    "p_pick_hits": round(100 * float(top3["hit"].mean()), 2),
                    "weeks_basket_gained_target": round(
                        100 * float((by_week["ret"] >= config["hit_return_fraction"]).mean()), 2
                    ),
                    "avg_net_pct_per_week": round(
                        100 * float(by_week["ret"].mean() - round_trip), 3
                    ),
                    "weeks": int(len(by_week)),
                },
                "base_rate_pct": round(100 * float(all_test["hit"].mean()), 2),
                "survivorship_note": "universe is today's long-history names; failed companies are absent",
            },
            indent=1,
        ),
        encoding="utf8",
    )
    print(f"Results: {output_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
