"""Run every finding of this research programme through the honesty layer.

Each result below was produced by a separate script and, at the time it was
produced, was individually correct and collectively misleading. This script
re-derives them and prints each one with its provenance, its trial count, its
regime split and its sub-period stability attached, then applies
:func:`forward.research.evidence.assess` and reports the verdict.

The output is the deliverable: a set of verdicts rather than a headline number.

Usage:
    ./.venv/Scripts/python.exe scripts/research_honest_verdicts.py
"""

# ruff: noqa: I001
from __future__ import annotations

import json
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from forward.research.evidence import (  # noqa: E402
    Evidence,
    UniverseProvenance,
    assess,
    buy_and_hold,
    regime_split,
    selection_bias,
    stability,
    summarise,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "research_honest_verdicts.json"


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------


def unbiased_close(config: dict) -> pd.DataFrame:
    """Every cache symbol with a long history — no liquidity selection."""
    series = {}
    data_directory = ROOT / config["daily_data_directory"]
    for path in sorted(data_directory.glob("*.parquet")):
        frame = pd.read_parquet(path, columns=["ts", "close"])
        if len(frame) < config["minimum_history_bars"]:
            continue
        frame = frame.copy()
        frame["ts"] = pd.to_datetime(frame["ts"])
        series[path.stem.upper()] = frame.set_index("ts")["close"]
    if not series:
        raise ValueError("No cache symbols meet the configured history gate.")
    return pd.DataFrame(series).sort_index()


def selected_close(config: dict) -> pd.DataFrame:
    panel_path = ROOT / config["selected_panel_file"]
    frame = pd.read_parquet(panel_path)
    frame["ts"] = pd.to_datetime(frame["ts"])
    close = frame.pivot_table(index="ts", columns="symbol", values="close").sort_index()
    if close.empty:
        raise ValueError(f"Configured selected panel is empty: {panel_path}.")
    return close


def cagr_and_dd(rets: pd.Series, config: dict) -> tuple[float, float]:
    if not np.isfinite(rets.to_numpy(dtype=float)).all():
        raise ValueError("Return series contains missing or non-finite values.")
    eq = (1 + rets).cumprod()
    dd = float((eq / eq.cummax() - 1).min() * 100)
    years = len(rets) / config["trading_days_per_year"]
    cagr = (eq.iloc[-1] ** (1 / years) - 1) * 100 if eq.iloc[-1] > 0 else -100.0
    return cagr, dd


def sharpe(rets: pd.Series, config: dict) -> float:
    if not np.isfinite(rets.to_numpy(dtype=float)).all():
        raise ValueError("Return series contains missing or non-finite values.")
    sd = rets.std()
    return (
        float(rets.mean() / sd * np.sqrt(config["trading_days_per_year"]))
        if sd > 0 else 0.0
    )


# --------------------------------------------------------------------------
# Candidates
# --------------------------------------------------------------------------


def high_vol_quintile(close: pd.DataFrame, config: dict) -> tuple[pd.Series, pd.Series]:
    """Long the highest-vol quintile, monthly, vs the equal-weight market."""
    returns = close.pct_change()
    monthly_period = config["monthly_period"]
    monthly = close.resample(monthly_period).last()
    vol_m = returns.rolling(config["high_volatility_lookback_days"]).std().shift(1).resample(monthly_period).last()
    fwd_m = monthly.pct_change().shift(-1)
    mkt_m = monthly.mean(axis=1).pct_change().shift(-1)

    rows = []
    for ts in monthly.index[config["warmup_months"] : -1]:
        v = vol_m.loc[:ts].iloc[-1].dropna()
        r = fwd_m.loc[ts].dropna()
        m = mkt_m.loc[ts]
        common = v.index.intersection(r.index)
        if len(common) < config["minimum_cross_section_symbols"] or pd.isna(m):
            continue
        top_count = max(
            int(len(common) * config["high_volatility_fraction"]), 1
        )
        top = v[common].rank().nlargest(top_count).index
        rows.append({"ts": ts, "s": float(r[list(top)].mean()), "m": float(m)})
    df = pd.DataFrame(rows).set_index("ts")
    return df["s"], df["m"]


def vol_scale_overlay(market: pd.Series, config: dict) -> pd.Series:
    """Hold less when recent vol is high. The best of the five overlays."""
    annual_volatility = (
        market.rolling(config["overlay_volatility_lookback_days"]).std()
        * np.sqrt(config["trading_days_per_year"])
    )
    weight = (
        config["overlay_target_annual_volatility"] / annual_volatility
    ).clip(upper=config["overlay_maximum_weight"]).shift(1)
    first_valid = weight.first_valid_index()
    if first_valid is None:
        raise ValueError("The configured window cannot produce overlay weights.")
    weight = weight.loc[first_valid:]
    if weight.isna().any():
        raise ValueError("Overlay volatility is missing inside the evaluation window.")
    return market.loc[first_valid:] * weight


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def main() -> int:
    """Write configured evidence judgments for prior research findings."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    config_path = parser.parse_args().config
    config = json.loads(config_path.read_text(encoding="utf-8"))
    print(f"Configuration: {config_path.resolve()}")
    output_path = ROOT / config["output_file"]
    print("loading panels...")
    unb = unbiased_close(config)
    sel = selected_close(config)
    common = unb.index.intersection(sel.index)
    if common.empty:
        raise ValueError("Selected and unselected panels have no common dates.")

    findings: list[tuple[str, Evidence]] = []

    # ---- Finding 1: the selection gap -------------------------------------
    bias = selection_bias(
        buy_and_hold(sel.loc[common]),
        buy_and_hold(unb.loc[common]),
        n_unbiased=unb.shape[1],
    )
    sel_ret = buy_and_hold(sel.loc[common])
    unb_ret = buy_and_hold(unb.loc[common])
    findings.append(("FINDING 1 — the survivorship selection gap", Evidence(
        claim="liquid-120 equal-weight index return",
        headline_pct=bias.selected_total_pct,
        provenance=UniverseProvenance(
            n_names=sel.shape[1],
            selected_on_full_window=True,
            history_bars=len(sel),
            survivorship_biased=True,
            note="names chosen by today's turnover from a cache with no delisted entries",
        ),
        n_trials=config["selection_gap_trial_count"],
        oos_sharpe=sharpe(sel_ret, config),
        benchmark_sharpe=sharpe(unb_ret, config),
        p_edge_real=float("nan"),
        bias=bias,
        max_drawdown_pct=cagr_and_dd(sel_ret, config)[1],
        benchmark_drawdown_pct=cagr_and_dd(unb_ret, config)[1],
    )))

    # ---- Finding 2: the high-vol factor -----------------------------------
    strat, market = high_vol_quintile(sel, config)
    blocks = pd.Series(strat.index, index=strat.index).dt.year
    spreads, labels = [], []
    for year in sorted(set(blocks)):
        mask = blocks == year
        if mask.sum() < config["minimum_yearly_observations"]:
            continue
        spreads.append(float((strat[mask] - market[mask]).mean() * 100))
        labels.append(str(year))
    _, dd_s = cagr_and_dd(strat, config)
    _, dd_m = cagr_and_dd(market, config)

    findings.append(("FINDING 2 — the high-volatility factor", Evidence(
        claim="long the top-vol quintile, monthly rebalance",
        headline_pct=(strat.add(1).cumprod().iloc[-1] - 1) * 100,
        provenance=UniverseProvenance(
            n_names=sel.shape[1], selected_on_full_window=True,
            history_bars=len(sel), survivorship_biased=True,
        ),
        n_trials=config["high_volatility_factor_trial_count"],
        oos_sharpe=sharpe(strat, config),
        benchmark_sharpe=sharpe(market, config),
        p_edge_real=float("nan"),
        bias=bias,
        regime=regime_split(strat, market),
        stability=stability(spreads, labels),
        max_drawdown_pct=dd_s,
        benchmark_drawdown_pct=dd_m,
    )))

    # ---- Finding 3: the vol-scaling overlay -------------------------------
    market_ew = buy_and_hold(unb)
    overlaid = vol_scale_overlay(market_ew, config)
    market_ew = market_ew.loc[overlaid.index]
    split_points = config["overlay_split_fractions"]
    calmar_advantages = []
    for frac in split_points:
        k = int(len(market_ew) * frac)
        tail = slice(k, None)
        c_o, d_o = cagr_and_dd(overlaid.iloc[tail], config)
        c_b, d_b = cagr_and_dd(market_ew.iloc[tail], config)
        cal_o = c_o / abs(d_o) if d_o < 0 else float("nan")
        cal_b = c_b / abs(d_b) if d_b < 0 else float("nan")
        calmar_advantages.append(cal_o - cal_b)
    c_o, d_o = cagr_and_dd(overlaid, config)
    c_b, d_b = cagr_and_dd(market_ew, config)

    findings.append(("FINDING 3 — the volatility-scaling overlay", Evidence(
        claim="hold the equal-weight index, scaled down when vol is high",
        headline_pct=(overlaid.add(1).cumprod().iloc[-1] - 1) * 100,
        provenance=UniverseProvenance(
            n_names=unb.shape[1], selected_on_full_window=False,
            history_bars=len(unb), survivorship_biased=False,
            note="no liquidity screen applied",
        ),
        n_trials=config["overlay_trial_count"],
        oos_sharpe=sharpe(overlaid, config),
        benchmark_sharpe=sharpe(market_ew, config),
        p_edge_real=float("nan"),
        regime=regime_split(overlaid, market_ew),
        stability=stability(
            calmar_advantages,
            [f"split@{int(f * 100)}%" for f in split_points],
            quantity="calmar_advantage",
        ),
        max_drawdown_pct=d_o,
        benchmark_drawdown_pct=d_b,
        notes=[
            "stability here is the Calmar advantage over buy-and-hold in the "
            f"second half, at {len(split_points)} different split points",
            f"full window Calmar {c_o / abs(d_o):.2f} vs buy-and-hold "
            f"{c_b / abs(d_b):.2f}",
        ],
    )))

    # ---- render -----------------------------------------------------------
    payload = {"generated_at": pd.Timestamp.utcnow().isoformat(), "findings": []}
    for title, ev in findings:
        verdict = assess(ev)
        print()
        print(title)
        print(summarise(ev))
        payload["findings"].append({
            "title": title,
            "claim": ev.claim,
            "headline_pct": ev.headline_pct,
            "universe": ev.provenance.describe(),
            "n_trials": ev.n_trials,
            "oos_sharpe": None if not np.isfinite(ev.oos_sharpe) else ev.oos_sharpe,
            "benchmark_sharpe": (
                None if not np.isfinite(ev.benchmark_sharpe) else ev.benchmark_sharpe
            ),
            "p_edge_real": (
                None if not np.isfinite(ev.p_edge_real) else ev.p_edge_real
            ),
            "max_drawdown_pct": abs(ev.max_drawdown_pct),
            "benchmark_drawdown_pct": abs(ev.benchmark_drawdown_pct),
            "selection_bias": None if ev.bias is None else {
                "selected_pct": ev.bias.selected_total_pct,
                "unbiased_pct": ev.bias.unbiased_total_pct,
                "gap_pct": ev.bias.gap_pct,
                "meaningful": ev.bias.meaningful,
            },
            "regime": None if ev.regime is None else {
                "is_leverage": ev.regime.is_leverage,
                "up_excess_pct": ev.regime.up_excess_pct,
                "down_excess_pct": ev.regime.down_excess_pct,
                "desc": ev.regime.describe(),
            },
            "stability": None if ev.stability is None else {
                "consistent": ev.stability.consistent,
                "positive_share": ev.stability.positive_share,
                "desc": ev.stability.describe(),
            },
            "notes": ev.notes,
            "verdict": {
                "credible": verdict.credible,
                "reasons": verdict.reasons,
            },
        })

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf8")
    print()
    print("=" * 74)
    print("Evidence report complete.")
    print(f"written: {output_path}")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
