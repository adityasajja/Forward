"""Walk-forward validation + volatility targeting for the gap-and-run strategy.

Key diagnostics:
1. Walk-forward: test the best config across multiple time folds
2. Volatility targeting: scale positions inversely to ATR
3. Parameter robustness: test neighboring configs
4. Monthly return decomposition
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import strategy_hunt as sh  # noqa: E402
from hunt_stops import compute_exit_returns, compute_signals, load_panel, run_strategy  # noqa: E402

p = print

def P(*a, **k):
    k.setdefault("flush", True)
    p(*a, **k)


def run_with_vol_targeting(close, high, low, signal, stop_pct, target_pct,
                            max_holding, k, step, trend_sma, costs,
                            vol_window, vol_target_annual, trading_days,
                            sessions_per_week, warmup_bars, min_rebalances,
                            risk_free_rate, minimum_annualized_volatility,
                            weekly_thresholds, display_decimals):
    """Run the strategy with inverse-volatility sizing and a volatility cap.

    Args:
        close: Closing prices, with dates in rows and instruments in columns.
        high: High prices with the same shape as ``close``.
        low: Low prices with the same shape as ``close``.
        signal: Strategy scores with the same shape as ``close``.
        stop_pct: Stop distance as a percent of entry price.
        target_pct: Target distance as a percent of entry price.
        max_holding: Maximum number of sessions to hold a position.
        k: Number of instruments to select at each entry.
        step: Number of sessions between entries.
        trend_sma: Trend moving-average window, or zero to disable the filter.
        costs: Cost charged per round trip.
        vol_window: Number of sessions used to estimate volatility.
        vol_target_annual: Maximum annual volatility for the portfolio.

    Returns:
        Performance metrics, or an error record when the sample is too short.

    Raises:
        ValueError: If the volatility window is too short or its data is invalid.
    """
    if vol_window < 2:
        raise ValueError("vol_window must contain at least two sessions.")
    n_dates, n_stocks = close.shape
    warmup = max(trend_sma, warmup_bars) if trend_sma > 0 else warmup_bars
    rebalance_dates = list(range(warmup, n_dates - max_holding, step))
    if len(rebalance_dates) < min_rebalances:
        return {"error": "insufficient rebalancing dates"}

    # Precompute daily returns
    daily_ret = np.full_like(close, 0.0)
    daily_ret[1:] = close[1:] / close[:-1] - 1.0

    all_returns = []
    total_trades = 0
    total_stops = 0
    total_targets = 0

    for t in rebalance_dates:
        scores = signal[t, :].copy()
        valid = close[t, :] > 0
        scores = np.where(valid, scores, -np.inf)

        if trend_sma > 0:
            # Compute SMA for each stock
            sma = np.full(n_stocks, np.nan)
            if t >= trend_sma:
                sma = close[t - trend_sma + 1:t + 1].mean(axis=0)
            mask = valid & (close[t] > sma)
            scores = np.where(mask, scores, -np.inf)

        top_k = min(k, int(np.sum(scores > -np.inf)))
        if top_k < k:
            continue

        picks = np.argpartition(-scores, top_k - 1)[:top_k]
        picks = picks[np.argsort(-scores[picks])]

        # Volatility targeting: compute realized vol for each picked stock
        vol_lookback = min(vol_window, t - 1)
        recent_ret = daily_ret[t - vol_lookback:t, :][:, picks]
        stock_vol = np.std(recent_ret, axis=0, ddof=1) * np.sqrt(trading_days)
        if (not np.isfinite(stock_vol).all()
                or (stock_vol <= minimum_annualized_volatility).any()):
            raise ValueError("selected instruments need valid, nonzero volatility data")

        # Target volatility scaling: weight inversely to stock vol
        inv_vol = 1.0 / stock_vol
        # Normalize to equal weight, then scale by vol target
        raw_weights = inv_vol / inv_vol.sum()

        # Scale by target vol: if market vol is high, reduce size
        # Use average stock vol to determine scaling
        avg_vol = np.mean(stock_vol)
        vol_scale = min(1.0, vol_target_annual / avg_vol)

        # Compute exit returns for each position
        exit_returns, n_stop, n_target, _ = compute_exit_returns(
            close, high, low, t, picks, max_holding, stop_pct, target_pct
        )

        # Apply position weights and vol targeting
        net_returns = exit_returns - costs
        # Weighted return: sum of (weight * return)
        portfolio_return = float(np.sum(raw_weights * vol_scale * net_returns))

        all_returns.append(portfolio_return)
        total_trades += k
        total_stops += n_stop
        total_targets += n_target

    if len(all_returns) < min_rebalances:
        return {"error": "insufficient trading days"}

    returns = np.array(all_returns)
    cum_ret = np.cumprod(1 + returns)
    total_return = float(cum_ret[-1] - 1)

    n_periods = len(returns)
    years = n_periods * step / trading_days
    cagr = (1 + total_return) ** (1.0 / years) - 1 if years > 0 else 0
    periods_per_year = trading_days / step
    arith_annual = float(np.mean(returns)) * periods_per_year
    vol = float(np.std(returns, ddof=1)) * np.sqrt(periods_per_year) if n_periods > 1 else 0
    sharpe = (arith_annual - risk_free_rate) / vol if vol > 0 else 0

    weekly_factor = sessions_per_week / step
    wr = returns * weekly_factor
    weekly_mean = round(100 * float(np.mean(wr)), display_decimals)
    weekly_std = round(100 * float(np.std(wr, ddof=1)), display_decimals)
    weekly_win = round(100 * float(np.mean(wr > 0)), display_decimals)
    weekly_ge2 = round(100 * float(np.mean(wr >= weekly_thresholds["2"])), display_decimals)
    weekly_ge3 = round(100 * float(np.mean(wr >= weekly_thresholds["3"])), display_decimals)
    weekly_ge5 = round(100 * float(np.mean(wr >= weekly_thresholds["5"])), display_decimals)

    running_max = np.maximum.accumulate(cum_ret)
    max_dd = float(np.min(cum_ret / running_max - 1)) if len(cum_ret) > 0 else 0

    position_win_rate = round(100 * total_targets / max(total_trades, 1), 1)
    position_stop_rate = round(100 * total_stops / max(total_trades, 1), 1)

    return {
        "cagr_pct": round(100 * cagr, 2),
        "sharpe": round(sharpe, 3),
        "vol_pct": round(vol, 2),
        "max_drawdown_pct": round(100 * max_dd, 2),
        "total_return_pct": round(100 * total_return, 2),
        "weekly_mean_pct": weekly_mean,
        "weekly_vol_pct": weekly_std,
        "weekly_win_rate_pct": weekly_win,
        "weeks_ge_2pct_pct": weekly_ge2,
        "weeks_ge_3pct_pct": weekly_ge3,
        "weeks_ge_5pct_pct": weekly_ge5,
        "num_trades": total_trades,
        "win_rate_pct": position_win_rate,
        "stop_rate_pct": position_stop_rate,
        "n_rebalances": n_periods,
    }


def main():
    """Run the configured gap-and-run robustness study."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    config_path = parser.parse_args().config
    config = json.loads(config_path.read_text(encoding="utf-8"))
    P(f"Configuration: {config_path.resolve()}")
    start = pd.Timestamp(config["start_date"])
    end = pd.Timestamp(config["end_date"])

    all_symbols = list(set(
        sh.parse_universe(ROOT / path)
        for path in config["universe_files"]
    ))
    P(f"Loading OHLCV for {len(all_symbols)} symbols...")
    t0 = time.time()
    panels, symbols_loaded, dates = load_panel(
        all_symbols, start, end, min_bars=config["minimum_bars"]
    )
    if len(dates) == 0 or not symbols_loaded:
        raise ValueError("Configured data range produced no usable OHLCV panel.")
    P(f"  Loaded {len(symbols_loaded)} stocks, {len(dates)} dates in {time.time()-t0:.1f}s")

    close = panels["close"].to_numpy(dtype=np.float64)
    high = panels["high"].to_numpy(dtype=np.float64)
    low = panels["low"].to_numpy(dtype=np.float64)
    open_ = panels["open"].to_numpy(dtype=np.float64)
    volume = panels["volume"].to_numpy(dtype=np.float64)

    n_dates = len(dates)
    mid = int(n_dates * config["oos_start_fraction"])
    P(f"\n  Full range: {dates[0].date()} → {dates[-1].date()} ({n_dates} bars)")

    # Precompute signals
    signals = compute_signals(close, high, low, open_, volume, len(symbols_loaded))

    # B&H benchmark (full period)
    oos_ret = np.nanmean(close[1:] / close[:-1] - 1, axis=1)
    bh_weekly = round(
        100 * float(np.nanmean(oos_ret)) * config["sessions_per_week"],
        config["display_decimal_places"],
    )
    P(f"\n  B&H (full, eq-weight): ~{bh_weekly}% weekly mean")

    # ================================================================
    # PART 1: Walk-forward validation of best config
    # ================================================================
    best_config = {
        **config["best_config"],
    }
    sig = signals["gap_run_5"]

    P(f"\n{'='*72}")
    P(f"WALK-FORWARD VALIDATION: {best_config['signal']}/sl{best_config['sl']}/tp{best_config['tp']}/"
      f"mh{best_config['mh']}/k{best_config['k']}/step{best_config['step']}/ts{best_config['tsma']}")
    P(f"{'='*72}")

    # Split into 4 folds
    fold_size = n_dates // config["fold_count"]
    folds = [(i * fold_size, (i + 1) * fold_size)
             for i in range(config["fold_count"] - 1)]
    folds.append(((config["fold_count"] - 1) * fold_size, n_dates))

    fold_results = []
    for i, (start_idx, end_idx) in enumerate(folds):
        fold_close = close[start_idx:end_idx + 1]
        fold_high = high[start_idx:end_idx + 1]
        fold_low = low[start_idx:end_idx + 1]
        fold_sig = sig[start_idx:end_idx + 1]
        fold_vol = volume[start_idx:end_idx + 1]

        r = run_strategy(fold_close, fold_high, fold_low, fold_sig,
                         best_config["sl"], best_config["tp"], best_config["mh"],
                         best_config["k"], best_config["step"], best_config["tsma"],
                         volume=fold_vol)
        if "error" in r:
            P(f"  Fold {i+1} ({dates[start_idx].date()} → {dates[end_idx].date()}): {r['error']}")
            continue
        fold_results.append(r)
        P(f"  Fold {i+1} ({dates[start_idx].date()} → {dates[end_idx].date()}): "
          f"wk={r['weekly_mean_pct']:+.3f}% Sharpe={r['sharpe']:.3f} DD={r['max_drawdown_pct']:.0f}% "
          f"trades={r['num_trades']} win={r['win_rate_pct']:.0f}%")

    if len(fold_results) >= config["minimum_valid_folds_for_summary"]:
        avg_wk = np.mean([r["weekly_mean_pct"] for r in fold_results])
        avg_sh = np.mean([r["sharpe"] for r in fold_results])
        P(f"\n  Walk-forward avg: wk={avg_wk:+.3f}% Sharpe={avg_sh:.3f}")
        P(f"  Consistent profitable folds: {sum(1 for r in fold_results if r['weekly_mean_pct'] > 0)}/{len(fold_results)}")
        P(f"  Consistent Sharpe>{config['profitable_fold_minimum_sharpe']} folds: "
          f"{sum(1 for r in fold_results if r['sharpe'] > config['profitable_fold_minimum_sharpe'])}/{len(fold_results)}")

    # ================================================================
    # PART 2: Volatility targeting vs equal weighting
    # ================================================================
    P(f"\n{'='*72}")
    P("VOLATILITY TARGETING vs EQUAL WEIGHTING")
    P(f"{'='*72}")

    configs_to_test = config["configs_to_test"]

    for cfg in configs_to_test:
        sig_name = cfg["signal"]
        sig_arr = signals[sig_name]

        # Evaluate the second half of the data.
        oos_close, oos_high, oos_low = close[mid:], high[mid:], low[mid:]
        oos_sig = sig_arr[mid:]
        oos_vol = volume[mid:]

        P(f"\n  {sig_name}/sl{cfg['sl']}/tp{cfg['tp']}/mh{cfg['mh']}/k{cfg['k']}/step{cfg['step']}/ts{cfg['tsma']}:")

        # Equal weighting (original)
        eq_r = run_strategy(oos_close, oos_high, oos_low, oos_sig,
                            cfg["sl"], cfg["tp"], cfg["mh"], cfg["k"], cfg["step"], cfg["tsma"],
                            volume=oos_vol)
        if "error" not in eq_r:
            P(f"    Equal weight:  wk={eq_r['weekly_mean_pct']:+.3f}% Sharpe={eq_r['sharpe']:.3f} "
              f"DD={eq_r['max_drawdown_pct']:.0f}% trades={eq_r['num_trades']} win={eq_r['win_rate_pct']:.0f}%")

        # Volatility targeting
        vol_r = run_with_vol_targeting(oos_close, oos_high, oos_low, oos_sig,
                                       cfg["sl"], cfg["tp"], cfg["mh"], cfg["k"], cfg["step"], cfg["tsma"],
                                       costs=config["cost_per_roundtrip"],
                                       vol_target_annual=config["vol_target_annual"],
                                       vol_window=config["vol_window"],
                                       trading_days=config["trading_days"],
                                       sessions_per_week=config["sessions_per_week"],
                                       warmup_bars=config["warmup_bars"],
                                       min_rebalances=config["minimum_rebalances"],
                                       risk_free_rate=config["annual_risk_free_rate"],
                                       minimum_annualized_volatility=config[
                                           "minimum_annualized_volatility"
                                       ],
                                       weekly_thresholds=config["weekly_thresholds"],
                                       display_decimals=config["display_decimal_places"])
        if "error" not in vol_r:
            P(f"    Vol targeting: wk={vol_r['weekly_mean_pct']:+.3f}% Sharpe={vol_r['sharpe']:.3f} "
              f"DD={vol_r['max_drawdown_pct']:.0f}% trades={vol_r['num_trades']} win={vol_r['win_rate_pct']:.0f}%")

        # Parameter robustness: neighboring configs
        P("    Param robustness (OOS):")
        for sl_d in config["stop_deltas"]:
            for tp_d in config["target_deltas"]:
                sl = cfg["sl"] + sl_d
                tp = cfg["tp"] + tp_d
                if sl <= config["minimum_stop_pct"] or tp <= config["minimum_target_pct"]:
                    continue
                r = run_strategy(oos_close, oos_high, oos_low, oos_sig,
                                 sl, tp, cfg["mh"], cfg["k"], cfg["step"], cfg["tsma"],
                                 volume=oos_vol)
                if "error" not in r:
                    tag = " *" if (sl_d == 0 and tp_d == 0) else "  "
                    P(f"      sl={sl}/tp={tp}: wk={r['weekly_mean_pct']:+.3f}% "
                      f"Sharpe={r['sharpe']:.3f} DD={r['max_drawdown_pct']:.0f}% trades={r['num_trades']}{tag}")

    # ================================================================
    # PART 3: Monthly return decomposition (best config)
    # ================================================================
    P(f"\n{'='*72}")
    P("MONTHLY RETURN DECOMPOSITION (best config OOS)")
    P(f"{'='*72}")

    best = configs_to_test[0]
    sig_arr = signals[best["signal"]]
    r = run_strategy(close[mid:], high[mid:], low[mid:], sig_arr[mid:],
                     best["sl"], best["tp"], best["mh"], best["k"], best["step"], best["tsma"],
                     volume=volume[mid:])

    if "error" not in r:
        # Compute monthly returns
        warmup = config["warmup_bars"]
        step = best["step"]
        all_dates = []
        all_returns = []
        n_oos = close[mid:].shape[0]

        for t in range(warmup, n_oos - best["mh"], step):
            scores = sig_arr[mid:][t, :].copy()
            valid = close[mid:][t, :] > 0
            scores = np.where(valid, scores, -np.inf)
            top_k = min(best["k"], int(np.sum(scores > -np.inf)))
            if top_k < best["k"]:
                continue
            picks = np.argpartition(-scores, top_k - 1)[:top_k]
            picks = picks[np.argsort(-scores[picks])]
            exit_r, ns, nt, nh = compute_exit_returns(
                close[mid:], high[mid:], low[mid:], t, picks, best["mh"],
                best["sl"], best["tp"]
            )
            net = np.mean(exit_r - config["cost_per_roundtrip"])
            all_dates.append(dates[mid + t])
            all_returns.append(net)

        if all_returns:
            wr = np.array(all_returns) * (config["sessions_per_week"] / step)
            dr = pd.DataFrame({"date": all_dates, "ret": wr})
            dr["month"] = dr["date"].apply(lambda x: x.to_period("M"))
            monthly = dr.groupby("month")["ret"].agg(["mean", "std", "count"])
            P("\n  Monthly OOS performance:")
            for m, row in monthly.iterrows():
                P(f"  {m}: avg={row['mean']:+.3f}% std={row['std']:.3f}% n={int(row['count'])}")
            P(f"\n  Overall: avg_weekly={wr.mean()*100:.3f}% std={wr.std()*100:.3f}% "
              f"min={wr.min()*100:+.3f}% max={wr.max()*100:+.3f}%")
            P(f"  P(positive week)={(wr > 0).mean()*100:.0f}%")

    # ================================================================
    # SUMMARY
    # ================================================================
    P(f"\n{'='*72}")
    P("FINAL SUMMARY")
    P(f"{'='*72}")

    # Best overall config with vol targeting
    P("\n  Best configs (OOS, with stops):")
    for cfg in configs_to_test:
        sig_arr = signals[cfg["signal"]]
        eq_r = run_strategy(close[mid:], high[mid:], low[mid:],
                            sig_arr[mid:], cfg["sl"], cfg["tp"], cfg["mh"],
                            cfg["k"], cfg["step"], cfg["tsma"],
                            volume=volume[mid:])
        vol_r = run_with_vol_targeting(close[mid:], high[mid:], low[mid:],
                                       sig_arr[mid:], cfg["sl"], cfg["tp"], cfg["mh"],
                                       cfg["k"], cfg["step"], cfg["tsma"],
                                       costs=config["cost_per_roundtrip"],
                                       vol_target_annual=config["vol_target_annual"],
                                       vol_window=config["vol_window"],
                                       trading_days=config["trading_days"],
                                       sessions_per_week=config["sessions_per_week"],
                                       warmup_bars=config["warmup_bars"],
                                       min_rebalances=config["minimum_rebalances"],
                                       risk_free_rate=config["annual_risk_free_rate"],
                                       minimum_annualized_volatility=config[
                                           "minimum_annualized_volatility"
                                       ],
                                       weekly_thresholds=config["weekly_thresholds"],
                                       display_decimals=config["display_decimal_places"])
        if "error" not in eq_r:
            P(f"\n  {cfg['signal']}/sl{cfg['sl']}/tp{cfg['tp']}/mh{cfg['mh']}/k{cfg['k']}/s{cfg['step']}/ts{cfg['tsma']}:")
            P(f"    Equal weight:     wk={eq_r['weekly_mean_pct']:+.3f}% Sharpe={eq_r['sharpe']:.3f} "
              f"DD={eq_r['max_drawdown_pct']:.0f}% win_week={eq_r['weekly_win_rate_pct']:.0f}%")
            P(f"    P(>=2%wk)={eq_r['weeks_ge_2pct_pct']}% P(>=5%wk)={eq_r['weeks_ge_5pct_pct']}%")
            if "error" not in vol_r:
                P(f"    Vol targeting:     wk={vol_r['weekly_mean_pct']:+.3f}% Sharpe={vol_r['sharpe']:.3f} "
                  f"DD={vol_r['max_drawdown_pct']:.0f}% win_week={vol_r['weekly_win_rate_pct']:.0f}%")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
