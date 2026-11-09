"""Is a 5-10% weekly return achievable on NSE equity?

This script does not search for a strategy. It measures the target itself:
what a 5%/week and 10%/week hurdle demands of a return distribution, and how
often the real NSE equity market has produced anything close to it.

Run:  ./.venv/Scripts/python.exe scripts/research_weekly_target.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "research_weekly_target.json"


def load(symbol: str, config: dict) -> pd.Series | None:
    """Load one configured symbol series and remove known reverted spikes."""
    path = ROOT / config["daily_data_directory"] / f"{symbol}.parquet"
    if not path.exists():
        raise FileNotFoundError(f"Required history for {symbol} is missing: {path}.")
    df = pd.read_parquet(path, columns=["ts", "close"])
    df = df.dropna(subset=["close"])
    df = df[df["close"] > 0].sort_values("ts")
    if len(df) < config["minimum_raw_history_bars"]:
        return None
    # The IIFL daily cache carries corrupt bars on 2021-09-15/16 (14 bars at
    # 3-5x the surrounding level). Left in, they fabricate +1000% weeks and
    # inflate every mean. Reuse the repo's reversion-keyed filter.
    from forward.research.data_hygiene import drop_reverting_spikes

    df, _dropped = drop_reverting_spikes(df)
    df = df[df["close"] > 0].sort_values("ts")
    if len(df) < config["minimum_raw_history_bars"]:
        return None
    s = pd.Series(df["close"].to_numpy(), index=pd.DatetimeIndex(df["ts"]))
    return s[~s.index.duplicated(keep="last")]


def weekly(close: pd.Series, config: dict) -> pd.Series:
    """Friday-to-Friday returns from a daily close series."""
    w = close.resample(config["week_period"]).last().dropna()
    return w.pct_change().dropna()


def describe_weekly(r: pd.Series, config: dict) -> dict:
    trading_weeks = config["trading_weeks_per_year"]
    yrs = len(r) / trading_weeks
    ann_vol = r.std() * np.sqrt(trading_weeks)
    report_thresholds = config["reported_return_fractions"]
    return {
        "weeks": int(len(r)),
        "years": round(yrs, 2),
        "mean_weekly_pct": round(100 * r.mean(), 4),
        "median_weekly_pct": round(100 * r.median(), 4),
        "sd_weekly_pct": round(100 * r.std(), 4),
        "ann_vol_pct": round(100 * ann_vol, 2),
        "best_week_pct": round(100 * r.max(), 2),
        "worst_week_pct": round(100 * r.min(), 2),
        "hit_ge_2pct": round(100 * (r >= report_thresholds[0]).mean(), 3),
        "hit_ge_5pct": round(100 * (r >= report_thresholds[1]).mean(), 3),
        "hit_ge_10pct": round(100 * (r >= report_thresholds[2]).mean(), 3),
        "weeks_ge_5pct_count": int((r >= report_thresholds[1]).sum()),
        "weeks_ge_10pct_count": int((r >= report_thresholds[2]).sum()),
    }


def hurdle_table(close: pd.Series, name: str, config: dict) -> dict:
    """What each weekly hurdle demands, and how the instrument actually did."""
    w = close.resample(config["week_period"]).last().dropna()
    total_years = (w.index[-1] - w.index[0]).days / 365.25
    total_ret = w.iloc[-1] / w.iloc[0] - 1
    cagr = (1 + total_ret) ** (1 / total_years) - 1
    r = w.pct_change().dropna()
    trading_weeks = config["trading_weeks_per_year"]
    ann_vol = r.std() * np.sqrt(trading_weeks)
    cagr_ratio = (1 + cagr) ** (1 / trading_weeks) - 1

    row = {
        "instrument": name,
        "years": round(total_years, 2),
        "total_return_pct": round(100 * total_ret, 2),
        "cagr_pct": round(100 * cagr, 2),
        "weekly_equivalent_pct": round(100 * cagr_ratio, 4),
        "ann_vol_pct": round(100 * ann_vol, 2),
        "realised_sharpe": round(cagr / ann_vol, 3) if ann_vol else None,
        "required_sharpe": {},
        "account_value_after_period": round(config["initial_capital"] * (1 + total_ret)),
    }
    for t in config["target_weekly_return_fractions"]:
        ann = (1 + t) ** trading_weeks - 1
        row["required_sharpe"][f"{int(t * 100)}pct_wk"] = {
            "annual_return_pct": round(100 * ann, 1),
            "multiple_in_1yr": round((1 + t) ** trading_weeks, 1),
            "sharpe_at_realised_vol": round(ann / ann_vol, 2) if ann_vol else None,
            "sharpe_at_configured_volatility": round(
                ann / config["comparison_annual_volatility"], 2
            ),
            "x_realised_cagr": round(ann / cagr, 1) if cagr > 0 else None,
        }
    return row


def bootstrap_paths(r: np.ndarray, config: dict) -> dict:
    """Bootstrap configured weekly paths from realised weekly returns."""
    bootstrap = config["bootstrap"]
    trading_weeks = config["trading_weeks_per_year"]
    rng = np.random.default_rng(bootstrap["seed"])
    draws = rng.choice(
        r,
        size=(bootstrap["path_count"], trading_weeks),
        replace=True,
    )
    terminal = np.prod(1 + draws, axis=1)
    out = {
        "n_paths": bootstrap["path_count"],
        "median_terminal_multiple": round(float(np.median(terminal)), 3),
        "pct_paths_doubling": round(
            100 * float((terminal >= bootstrap["doubling_multiple"]).mean()), 2
        ),
        "best_path_multiple": round(float(terminal.max()), 2),
        "pct_paths_ge_5pct_wk": round(
            100
            * float(
                (
                    terminal >= (1 + config["target_weekly_return_fractions"][0]) ** trading_weeks
                ).mean()
            ),
            4,
        ),
        "pct_paths_ge_10pct_wk": round(
            100
            * float(
                (
                    terminal >= (1 + config["target_weekly_return_fractions"][1]) ** trading_weeks
                ).mean()
            ),
            5,
        ),
    }
    # Continuous drag: IID resampling lets a lucky path stack; report the
    # compounded return of the *mean* log return, which is what a repeatable
    # edge would have to beat.
    out["geometric_annual_pct_from_bootstrap"] = round(
        100 * (np.exp(trading_weeks * np.log1p(draws).mean()) - 1), 2
    )
    return out


def best_rolling_window(close: pd.Series, config: dict) -> dict:
    weeks = config["trading_weeks_per_year"]
    target = config["target_weekly_return_fractions"][0]
    w = close.resample(config["week_period"]).last().dropna()
    roll = w / w.shift(weeks) - 1
    roll = roll.dropna()
    if roll.empty:
        raise ValueError("The instrument has too little data for the configured rolling window.")
    i = int(np.nanargmax(roll.to_numpy()))
    return {
        "best_52w_return_pct": round(100 * float(roll.iloc[i]), 2),
        "best_52w_end": str(roll.index[i].date()),
        "pct_annual_windows_ge_target": round(
            100 * float((roll >= (1 + target) ** weeks - 1).mean()), 4
        ),
        "annual_windows": int(len(roll)),
    }


def stock_week_universe(symbols: list[str], start, end, config: dict) -> dict:
    """Measure weekly target hits for the configured stock universe."""
    thresholds = config["reported_return_fractions"]
    rows = []
    for sym in symbols:
        s = load(sym, config)
        if s is None:
            continue
        s = s.loc[(s.index >= start) & (s.index <= end)]
        if len(s) < config["minimum_window_bars"]:
            continue
        r = weekly(s, config)
        if len(r) < config["minimum_stock_week_history"]:
            continue
        rows.append(r)
    if not rows:
        raise ValueError("No universe symbols meet the configured stock-week history.")
    allr = pd.concat(rows, ignore_index=True)
    return {
        "n_symbols": len(rows),
        "n_stock_weeks": int(len(allr)),
        "pct_stock_weeks_ge_5pct": round(100 * float((allr >= thresholds[1]).mean()), 3),
        "pct_stock_weeks_ge_10pct": round(100 * float((allr >= thresholds[2]).mean()), 3),
        "pct_stock_weeks_ge_20pct": round(100 * float((allr >= thresholds[3]).mean()), 3),
        "median_stock_week_pct": round(100 * float(allr.median()), 4),
        # If you held 10 names at equal weight, how often is the *portfolio*
        # up 5% in a week? Approximate by sampling 10 stock-weeks per week.
        "note": "stock-weeks pooled across names and dates; not a portfolio return",
    }


def portfolio_simulation(start, end, config: dict) -> dict:
    """Weekly return of an equal-weight portfolio of N randomly chosen names.

    This is the decisive test. 14.6% of individual stock-weeks clear +5%, so the
    raw material for a 5% week plainly exists. What matters is whether a
    *portfolio* you could have assembled in advance clears it.
    """
    simulation = config["portfolio_simulation"]
    uni = ROOT / config["universe_directory"]
    syms: list[str] = []
    for f in config["universe_files"]:
        p = uni / f
        syms += [x.strip().upper() for x in p.read_text().replace("\n", ",").split(",")]
    syms = sorted({s for s in syms if s})

    cols = {}
    for sym in syms:
        s = load(sym, config)
        if s is None:
            continue
        s = s.loc[(s.index >= start) & (s.index <= end)]
        if len(s) < config["minimum_window_bars"]:
            continue
        cols[sym] = weekly(s, config)
    if not cols:
        raise ValueError("No universe symbols have usable weekly history.")
    W = pd.DataFrame(cols).sort_index()
    W = W.loc[:, W.notna().sum() >= config["minimum_weekly_bars_per_symbol"]]
    # A weekly move beyond +-300% is a corporate-action artefact, not a return.
    # Count and report them rather than silently clipping.
    max_weekly_move = simulation["maximum_absolute_weekly_return"]
    n_extreme = int((W.abs() > max_weekly_move).sum().sum())
    W = W.mask(W.abs() > max_weekly_move)
    # Drop all-NaN rows; keep the rest (nanmean tolerates missing names).
    W = W.loc[W.notna().sum(axis=1) >= config["minimum_symbols_per_week"]]
    if W.empty:
        raise ValueError("No portfolio weeks meet the configured cross-section threshold.")
    weeks = W.index
    M = W.to_numpy(dtype=float)
    n_w, n_s = M.shape

    rng = np.random.default_rng(simulation["seed"])
    out: dict = {
        "n_symbols": int(n_s),
        "n_weeks": int(n_w),
        "extreme_weeks_dropped": n_extreme,
        "window": [str(weeks[0].date()), str(weeks[-1].date())],
    }
    for portfolio_size in simulation["sizes"]:
        if portfolio_size > n_s:
            raise ValueError(
                f"Configured portfolio size {portfolio_size} exceeds {n_s} available symbols."
            )
        draws = np.empty((simulation["draw_count"], n_w), dtype=float)
        for d in range(simulation["draw_count"]):
            pick = rng.choice(n_s, size=portfolio_size, replace=False)
            sub = M[:, pick]
            with np.errstate(invalid="ignore"):
                draws[d] = np.nanmean(sub, axis=1)
        flat = draws[np.isfinite(draws)]
        if not flat.size:
            raise ValueError(f"No finite weekly returns for portfolio size {portfolio_size}.")
        out[f"N={portfolio_size}"] = {
            "mean_weekly_pct": round(100 * float(flat.mean()), 4),
            "median_weekly_pct": round(100 * float(np.median(flat)), 4),
            "sd_weekly_pct": round(100 * float(flat.std()), 4),
            "pct_weeks_ge_5pct": round(
                100 * float((flat >= config["reported_return_fractions"][1]).mean()), 3
            ),
            "pct_weeks_ge_2pct": round(
                100 * float((flat >= config["reported_return_fractions"][0]).mean()), 3
            ),
            "pct_weeks_le_minus5pct": round(
                100 * float((flat <= config["reported_downside_fraction"]).mean()), 3
            ),
            "best_week_pct": round(100 * float(flat.max()), 2),
        }

    # Ceiling: hold the single best-performing name every week (perfect
    # foresight). Not achievable — it bounds the idea, it does not describe it.
    best = np.nanmax(M, axis=1)
    out["oracle_single_best_name"] = {
        "mean_weekly_pct": round(100 * float(np.nanmean(best)), 2),
        "pct_weeks_ge_5pct": round(
            100 * float((best >= config["reported_return_fractions"][1]).mean()), 2
        ),
        "note": "perfect foresight of the week's top name; unachievable upper bound",
    }
    # Median name — the "pick an average stock" counterfactual.
    med = np.nanmedian(M, axis=1)
    out["median_name"] = {
        "mean_weekly_pct": round(100 * float(np.nanmean(med)), 3),
        "pct_weeks_ge_5pct": round(
            100 * float((med >= config["reported_return_fractions"][1]).mean()), 2
        ),
    }
    return out


def required_edge(port: dict, config: dict) -> dict:
    """Express the hurdle as the Sharpe a strategy would have to sustain.

    Uses the random portfolio's own volatility, so the comparison is against
    something that actually exists rather than against a made-up vol number.
    """
    n_key = config["portfolio_simulation"]["required_edge_baseline"]
    base = port[n_key]
    mu = base["mean_weekly_pct"] / 100
    sd = base["sd_weekly_pct"] / 100
    out: dict = {
        "baseline": n_key,
        "baseline_mean_weekly_pct": base["mean_weekly_pct"],
        "baseline_sd_weekly_pct": base["sd_weekly_pct"],
    }
    for t in config["target_weekly_return_fractions"]:
        shift = t - mu
        weekly_sharpe = shift / sd
        out[f"{int(t * 100)}pct_wk"] = {
            "extra_weekly_return_needed_pct_points": round(100 * shift, 2),
            "in_weekly_sd_units": round(weekly_sharpe, 3),
            "annualised_sharpe_required": round(
                weekly_sharpe * np.sqrt(config["trading_weeks_per_year"]), 2
            ),
            "t_stat_over_52w": round(weekly_sharpe * np.sqrt(config["trading_weeks_per_year"]), 2),
        }
    return out


def main() -> int:
    """Write the configured weekly target feasibility report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    config_path = parser.parse_args().config
    config = json.loads(config_path.read_text(encoding="utf-8"))
    print(f"Configuration: {config_path.resolve()}")
    output_path = ROOT / config["output_file"]
    start = pd.Timestamp(config["history_start_date"])
    end = pd.Timestamp(config["history_end_date"])
    trading_weeks = config["trading_weeks_per_year"]
    targets = config["target_weekly_return_fractions"]
    report: dict = {"generated": pd.Timestamp.now().isoformat(timespec="seconds")}

    # --- 1. The target itself -------------------------------------------------
    report["hurdle_math"] = {
        f"{int(t * 100)}pct_per_week": {
            "annual_return_pct": round(100 * ((1 + t) ** trading_weeks - 1), 1),
            "multiple_in_1yr": round((1 + t) ** trading_weeks, 1),
            "multiple_in_3yr": round((1 + t) ** (3 * trading_weeks), 0),
            "account_value_in_1yr": round(config["initial_capital"] * (1 + t) ** trading_weeks),
        }
        for t in targets
    }

    # --- 2. Real instruments --------------------------------------------------
    instruments = {}
    for sym in config["instruments"]:
        s = load(sym, config)
        if s is None:
            raise ValueError(f"Insufficient configured history for instrument {sym}.")
        instruments[sym] = hurdle_table(s, sym, config)
        instruments[sym]["weekly_distribution"] = describe_weekly(weekly(s, config), config)
        instruments[sym]["best_rolling"] = best_rolling_window(s, config)
    report["instruments"] = instruments

    # --- 3. Bootstrap ---------------------------------------------------------
    nb = load(config["instruments"][0], config)
    if nb is None:
        raise ValueError("The bootstrap instrument does not meet the configured history gate.")
    report["bootstrap_instrument"] = bootstrap_paths(weekly(nb, config).to_numpy(), config)

    # --- 4. The universe as individual names ---------------------------------
    uni = ROOT / config["universe_directory"]
    syms: list[str] = []
    for f in config["universe_files"]:
        p = uni / f
        syms += [x.strip().upper() for x in p.read_text().replace("\n", ",").split(",")]
    syms = sorted({s for s in syms if s})
    if not syms:
        raise ValueError("Configured universe files contain no symbols.")
    report["individual_stocks"] = stock_week_universe(syms, start, end, config)
    report["random_portfolios"] = portfolio_simulation(start, end, config)
    report["required_edge"] = required_edge(report["random_portfolios"], config)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    print(f"\nwrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
