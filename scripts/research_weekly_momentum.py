"""Can weekly momentum selection deliver 5% per week across a portfolio?

The claim under test, verbatim from the request:

    "we should be able to filter and pick stocks that can gain momentum in the
    coming week. it's okay if all the stocks that we select don't perform but
    cumulative I should be earning 5% each week across the portfolio."

That is a testable statement, so this script tests it rather than argues about
it. Three things make it a fair test:

1. **The grid is pre-registered below, before any result is seen.** Every
   combination in it counts as a trial, and the trial count is reported. If the
   grid is widened later to find a winner, that shows up in the denominator.
2. **Every configuration is scored against a matched random control.** The
   control selects the *same number of names on the same dates* with the
   ranking destroyed — same concentration, same turnover, same costs, same
   exposure. If momentum beats it, the ranking contributed; if it does not, the
   configuration is a volatility choice wearing a signal's name.
3. **The cost model is the realistic one** (`IndianDeliveryCosts`, ~0.283% of
   turnover per round trip, dominated by STT at 0.1% per leg). A weekly
   rebalance pays that every week.

The decisive output is not a cumulative return. It is the **weekly return
distribution**, and specifically the pair

    P(week >= +5%)   versus   P(week <= -5%)

Because a rule that reaches +5% often but -5% just as often has not found an
edge, it has bought variance. The request accepts losers; it does not accept
that the winners and losers are the same size.

Usage::

    .venv/Scripts/python.exe scripts/research_weekly_momentum.py
    .venv/Scripts/python.exe scripts/research_weekly_momentum.py --universes smallcap250
"""

from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from forward.research.hunt import Costs

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "research_weekly_momentum.json"


def universe_symbols(name: str, config: dict) -> list[str]:
    """Read one required symbol universe from its configured file."""
    path = ROOT / config["universe_directory"] / config["universes"][name]
    # Single-line comma-separated, not one per line — `.split()` returns one
    # giant token and yields nothing, which reads as "no data".
    raw = path.read_text().replace("\n", ",")
    symbols = sorted({s.strip().upper() for s in raw.split(",") if s.strip()})
    if not symbols:
        raise ValueError(f"Configured universe {name!r} is empty: {path}.")
    return symbols


def load_weekly(
    symbol: str, start: pd.Timestamp, end: pd.Timestamp, config: dict
) -> pd.Series | None:
    """Friday-close series for one symbol, hygiene-filtered."""
    path = ROOT / config["daily_data_directory"] / f"{symbol}.parquet"
    if not path.exists():
        raise FileNotFoundError(f"Required history for {symbol} is missing: {path}.")
    df = pd.read_parquet(path, columns=["ts", "close"])
    df = df.dropna(subset=["close"])
    df = df[df["close"] > 0].sort_values("ts")
    if len(df) < config["minimum_raw_history_bars"]:
        raise ValueError(f"Symbol {symbol} has fewer than the required raw history bars.")
    from forward.research.data_hygiene import drop_reverting_spikes

    df, _ = drop_reverting_spikes(df)
    df = df[df["close"] > 0].sort_values("ts")
    if len(df) < config["minimum_raw_history_bars"]:
        raise ValueError(f"Symbol {symbol} has fewer than the required clean history bars.")
    s = pd.Series(df["close"].to_numpy(), index=pd.DatetimeIndex(df["ts"]))
    s = s[~s.index.duplicated(keep="last")]
    s = s.loc[(s.index >= start) & (s.index <= end)]
    if len(s) < config["minimum_window_bars"]:
        raise ValueError(f"Symbol {symbol} has too few bars inside the configured date window.")
    return s.resample(config["week_period"]).last().dropna()


def build_matrix(symbols: list[str], start: pd.Timestamp, end: pd.Timestamp, config: dict):
    cols = {}
    for sym in symbols:
        cols[sym] = load_weekly(sym, start, end, config)
    if not cols:
        raise ValueError("The configured universe has no valid weekly history.")
    W = pd.DataFrame(cols).sort_index()
    W = W.loc[:, W.notna().sum() >= config["minimum_weekly_bars_per_symbol"]]
    W = W.loc[W.notna().sum(axis=1) >= config["minimum_symbols_per_week"]]
    if W.empty:
        raise ValueError("No weeks meet the configured cross-section requirements.")
    return W, W.pct_change()


def weekly_metrics(returns: np.ndarray, config: dict) -> dict:
    r = returns[np.isfinite(returns)]
    if len(r) < config["minimum_metric_weeks"]:
        raise ValueError("Return history is shorter than the configured metric window.")
    equity = np.cumprod(1 + r)
    peak = np.maximum.accumulate(equity)
    dd = equity / peak - 1
    trading_weeks = config["trading_weeks_per_year"]
    years = len(r) / trading_weeks
    total = float(equity[-1] - 1)
    cagr = (1 + total) ** (1 / years) - 1 if years > 0 else 0.0
    sd = float(r.std())
    return {
        "weeks": int(len(r)),
        "mean_weekly_pct": round(100 * float(r.mean()), 4),
        "median_weekly_pct": round(100 * float(np.median(r)), 4),
        "sd_weekly_pct": round(100 * sd, 4),
        "total_return_pct": round(100 * total, 2),
        "cagr_pct": round(100 * cagr, 2),
        "ann_vol_pct": round(100 * sd * np.sqrt(trading_weeks), 2),
        "sharpe": round(cagr / (sd * np.sqrt(trading_weeks)), 3) if sd > 0 else None,
        "max_drawdown_pct": round(100 * float(dd.min()), 2),
        "hit_ge_5pct": round(100 * float((r >= config["reported_return_thresholds"][0]).mean()), 3),
        "hit_ge_2pct": round(100 * float((r >= config["reported_return_thresholds"][1]).mean()), 3),
        "hit_ge_1pct": round(100 * float((r >= config["reported_return_thresholds"][2]).mean()), 3),
        "hit_le_minus5pct": round(
            100 * float((r <= config["reported_downside_thresholds"][0]).mean()), 3
        ),
        "hit_le_minus2pct": round(
            100 * float((r <= config["reported_downside_thresholds"][1]).mean()), 3
        ),
        "best_week_pct": round(100 * float(r.max()), 2),
        "worst_week_pct": round(100 * float(r.min()), 2),
        "weeks_ge_5pct": int((r >= config["reported_return_thresholds"][0]).sum()),
    }


def simulate(
    W: pd.DataFrame,
    R: pd.DataFrame,
    lookback: int,
    skip: int,
    top_n: int,
    seed: int | None,
    exec_lag: int,
    config: dict,
) -> np.ndarray | None:
    """Weekly returns of an equal-weight top-N momentum book.

    Signal at week ``t`` is the return from ``t-skip-lookback`` to ``t-skip``.
    ``exec_lag=0`` trades at that same Friday close; ``exec_lag=1`` defers the
    trade a week, which is the conservative reading of "you see the signal and
    then act".
    """
    prices = W.to_numpy(dtype=float)
    rets = R.to_numpy(dtype=float)
    n_w, n_s = prices.shape
    if n_w <= lookback + skip + exec_lag + config["minimum_metric_weeks"]:
        raise ValueError(
            f"Insufficient weekly history for lookback={lookback}, skip={skip}, "
            f"execution lag={exec_lag}."
        )

    rng = np.random.default_rng(seed)
    signal = np.full((n_w, n_s), np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        signal = prices / np.roll(prices, skip + lookback, axis=0) - 1.0
    signal[: skip + lookback] = np.nan

    out = np.full(n_w, np.nan)
    round_trip_cost = Costs(**config["delivery_costs"]).round_trip_pct() / 100
    prev: set[int] = set()
    for t in range(n_w - 1):
        entry_week = t + exec_lag
        if entry_week + 1 >= n_w:
            break
        row = signal[t]
        ok = np.isfinite(row) & np.isfinite(rets[entry_week + 1])
        candidates = np.flatnonzero(ok)
        if candidates.size < top_n:
            continue
        if seed is None:
            scores = row[candidates]
            order = np.argsort(-scores, kind="stable")
            picks = set(candidates[order[:top_n]].tolist())
        else:
            picks = set(rng.choice(candidates, size=top_n, replace=False).tolist())
        nxt = rets[entry_week + 1]
        gross = float(np.nanmean(nxt[list(picks)]))
        turnover = 1.0 if not prev else len(picks - prev) / float(top_n)
        out[entry_week + 1] = gross - turnover * round_trip_cost
        prev = picks
    return out


def oracle(W: pd.DataFrame, R: pd.DataFrame, top_n: int) -> np.ndarray:
    """Hold the top-N names by *realised* next-week return. Unachievable ceiling."""
    rets = R.to_numpy(dtype=float)
    n_w, _ = rets.shape
    out = np.full(n_w, np.nan)
    for t in range(1, n_w):
        row = rets[t]
        ok = np.isfinite(row)
        if ok.sum() < top_n:
            continue
        out[t] = float(np.mean(np.sort(row[ok])[-top_n:]))
    return out


def leverage_analysis(mean_weekly_pct: float, max_dd_pct: float, config: dict) -> dict:
    """What leverage bridges the gap to 5%/week, and what it does to the floor?

    This is the honest answer to "so lever it". Leverage multiplies the return
    *and* the drawdown by the same factor, and the borrowed money costs
    something. Solving for the leverage ``L`` that nets the target:

        L * gross_annual - (L - 1) * borrow_rate = target_annual

    The number that matters is not the required ``L``. It is ``ruin_dd_pct``:
    the unlevered drawdown that wipes the account at that leverage. If the
    strategy has ever drawn down more than that, the plan is not risky — it is
    arithmetically impossible.
    """
    weeks_per_year = config["trading_weeks_per_year"]
    gross_annual = mean_weekly_pct / 100.0 * weeks_per_year
    target_weekly_pct = config["target_weekly_return_pct"]
    target_annual = target_weekly_pct / 100.0 * weeks_per_year
    borrow_rate_pct = config["borrow_rate_pct"]
    borrow = borrow_rate_pct / 100.0
    denom = gross_annual - borrow
    if denom <= 0:
        return {
            "leverage_required": None,
            "reason": "gross edge is below the cost of borrowing — no leverage reaches the target",
        }
    L = (target_annual + borrow) / denom
    return {
        "gross_edge_annual_pct": round(100 * gross_annual, 1),
        "target_annual_pct": round(100 * target_annual, 1),
        "borrow_rate_pct": borrow_rate_pct,
        "leverage_required": round(L, 2),
        "financing_drag_annual_pct": round(100 * (L - 1) * borrow, 1),
        "unlevered_max_dd_pct": max_dd_pct,
        "levered_max_dd_pct": round(max_dd_pct * L, 1),
        "ruin_dd_pct": round(-100.0 / L, 2),
        "ruin": max_dd_pct * L <= -100.0,
        "note": "ruin_dd_pct is the unlevered drawdown that wipes the account at "
        "this leverage; compare it to unlevered_max_dd_pct",
    }


def main() -> int:
    """Run the pre-registered study with its named configuration."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", type=Path, default=CONFIG_PATH)
    ap.add_argument("--universes", help="Comma-separated universe names from config.")
    ap.add_argument("--exec-lag", type=int, help="Override configured execution lag.")
    args = ap.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    print(f"Configuration: {args.config.resolve()}")
    start = pd.Timestamp(config["history_start_date"])
    end = pd.Timestamp(config["history_end_date"])
    execution_lag = config["execution_lag_weeks"] if args.exec_lag is None else args.exec_lag
    universe_names = config["universe_names"]
    if args.universes is not None:
        universe_names = [name.strip() for name in args.universes.split(",") if name.strip()]
        if not universe_names:
            raise ValueError("Pass at least one configured universe name.")
    grid = config["momentum_grid"]
    control_seeds = config["control_seeds"]
    round_trip_cost = Costs(**config["delivery_costs"]).round_trip_pct() / 100
    combos = 1
    for v in grid.values():
        combos *= len(v)

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "claim_under_test": (
            "filter and pick stocks that gain momentum in the coming week; "
            "losers acceptable, cumulative 5% per week across the portfolio"
        ),
        "pre_registered_grid": grid,
        "control": {"kind": "random selection, same N, same dates", "seeds": control_seeds},
        "cost_model": {
            "round_trip_pct_of_turnover": round(100 * round_trip_cost, 4),
            "source": "Configured delivery cost model",
        },
        "window": [str(start.date()), str(end.date())],
        "combos_per_universe": combos,
        "exec_lag_weeks": execution_lag,
        "universes": {},
    }

    for name in universe_names:
        if name not in config["universes"]:
            raise ValueError(f"Universe {name!r} is missing from the config.")
        syms = universe_symbols(name, config)
        W, R = build_matrix(syms, start, end, config)

        # Buy & hold over exactly the same window and names, equal weight.
        bh = R.mean(axis=1).to_numpy()
        bh_metrics = weekly_metrics(bh, config)

        print(
            f"\n══ {name}: {W.shape[1]} names, {W.shape[0]} weeks, "
            f"{W.index[0]:%Y-%m-%d} → {W.index[-1]:%Y-%m-%d}"
        )
        print(
            f"   buy & hold equal weight: {bh_metrics['total_return_pct']:+.2f}%  "
            f"CAGR {bh_metrics['cagr_pct']:.2f}%  Sharpe {bh_metrics['sharpe']:.3f}  "
            f"mean {bh_metrics['mean_weekly_pct']:+.4f}%/wk  "
            f"P(>={config['reported_return_thresholds'][0]:.0%})="
            f"{bh_metrics['hit_ge_5pct']:.2f}%  "
            f"P(<={config['reported_downside_thresholds'][0]:.0%})="
            f"{bh_metrics['hit_le_minus5pct']:.2f}%"
        )

        entry: dict = {
            "symbols": int(W.shape[1]),
            "weeks": int(W.shape[0]),
            "buy_and_hold": bh_metrics,
            "configs": [],
            "oracle": {},
        }

        for top_n in grid["top_n"]:
            orc = oracle(W, R, top_n)
            entry["oracle"][f"top{top_n}"] = weekly_metrics(orc, config)

        trials = 0
        for lookback in grid["lookback_weeks"]:
            for skip in grid["skip_weeks"]:
                for top_n in grid["top_n"]:
                    trials += 1
                    r = simulate(W, R, lookback, skip, top_n, None, execution_lag, config)
                    m = weekly_metrics(r, config)
                    ctrl = []
                    for seed in control_seeds:
                        cr = simulate(W, R, lookback, skip, top_n, seed, execution_lag, config)
                        ctrl.append(weekly_metrics(cr, config))
                    ctrl_mean = (
                        round(statistics.fmean(c["mean_weekly_pct"] for c in ctrl), 4)
                        if ctrl
                        else None
                    )
                    ctrl_sd = (
                        round(statistics.stdev(c["mean_weekly_pct"] for c in ctrl), 4)
                        if len(ctrl) > 1
                        else None
                    )
                    z = None
                    if ctrl_mean is not None and ctrl_sd and ctrl_sd > 1e-9:
                        z = round((m["mean_weekly_pct"] - ctrl_mean) / ctrl_sd, 2)
                    cfg = {
                        "lookback_weeks": lookback,
                        "skip_weeks": skip,
                        "top_n": top_n,
                        "metrics": m,
                        "control": {
                            "seeds": len(ctrl),
                            "mean_weekly_pct": ctrl_mean,
                            "sd_weekly_pct": ctrl_sd,
                            "hit_ge_5pct": round(
                                statistics.fmean(c["hit_ge_5pct"] for c in ctrl), 3
                            )
                            if ctrl
                            else None,
                            "hit_le_minus5pct": round(
                                statistics.fmean(c["hit_le_minus5pct"] for c in ctrl), 3
                            )
                            if ctrl
                            else None,
                            "sharpe": round(
                                statistics.fmean(
                                    c["sharpe"] for c in ctrl if c["sharpe"] is not None
                                ),
                                3,
                            )
                            if ctrl
                            else None,
                        },
                        "z_vs_control": z,
                    }
                    entry["configs"].append(cfg)
                    print(
                        f"   L={lookback:2d}w skip={skip} N={top_n:2d}  "
                        f"mean {m['mean_weekly_pct']:+.3f}%/wk  "
                        f"P(≥5%)={m['hit_ge_5pct']:5.2f}%  "
                        f"P(≤-5%)={m['hit_le_minus5pct']:5.2f}%  "
                        f"tot {m['total_return_pct']:+9.2f}%  "
                        f"Sh {m['sharpe']:+.2f}  "
                        f"ctrl {ctrl_mean:+.3f}  z={z}"
                    )

        entry["trials"] = trials
        cfgs = entry["configs"]
        if cfgs:
            best = max(cfgs, key=lambda c: c["metrics"]["mean_weekly_pct"])
            entry["best_mean_weekly"] = {
                k: best[k] for k in ("lookback_weeks", "skip_weeks", "top_n", "z_vs_control")
            }
            entry["best_mean_weekly"]["mean_weekly_pct"] = best["metrics"]["mean_weekly_pct"]
            entry["max_hit_ge_5pct"] = max(c["metrics"]["hit_ge_5pct"] for c in cfgs)
            entry["max_mean_weekly_pct"] = max(c["metrics"]["mean_weekly_pct"] for c in cfgs)
            entry["n_configs_beating_control"] = sum(
                1
                for c in cfgs
                if c["z_vs_control"] is not None
                and c["z_vs_control"] >= config["control_significance_z"]
            )
            entry["n_configs_with_mean_ge_5pct"] = sum(
                1
                for c in cfgs
                if c["metrics"]["mean_weekly_pct"] >= config["target_weekly_return_pct"]
            )
            entry["leverage_to_reach_5pct_wk"] = leverage_analysis(
                best["metrics"]["mean_weekly_pct"],
                best["metrics"]["max_drawdown_pct"],
                config,
            )
        payload["universes"][name] = entry

    out = ROOT / config["output_file"]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf8")
    print(f"\nwrote {out}")

    print("\n── summary ─────────────────────────────────────────────")
    for name, e in payload["universes"].items():
        if not e.get("configs"):
            continue
        print(
            f"{name}: {e['trials']} configs | best mean "
            f"{e['max_mean_weekly_pct']:+.3f}%/wk | max P(≥"
            f"{config['reported_return_thresholds'][0]:.0%}) "
            f"{e['max_hit_ge_5pct']:.2f}% | beating control at "
            f"z={config['control_significance_z']}: "
            f"{e['n_configs_beating_control']}/{len(e['configs'])} | "
            f"mean ≥{config['target_weekly_return_pct']:g}%/wk: "
            f"{e['n_configs_with_mean_ge_5pct']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
