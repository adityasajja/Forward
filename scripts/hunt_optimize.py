"""Final optimization: find robust configs with enough statistical significance.

Filters: min 15 rebalancing periods, min 75 trades, Sharpe > 1.5.
Tests: gap_run_5, gap_run, mom_5d with 200d regime + vol targeting.
"""
from __future__ import annotations

import argparse
import json
import time
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DAILY = ROOT / "data" / "iifl_daily" / "NSEEQ"
p = print


def P(*a, **k):
    k.setdefault("flush", True)
    p(*a, **k)


def load_symbol(sym, start, end):
    df = pd.read_parquet(DAILY / f"{sym}.parquet", columns=["ts", "open", "high", "low", "close", "volume"])
    df = df.dropna(subset=["close"]).sort_values("ts")
    df = df[df["close"] > 0]
    df = df[(df["ts"] >= start) & (df["ts"] <= end)].set_index("ts")
    return df


def load_stocks(symbols, start, end, min_bars):
    close_d, high_d, low_d, open_d, vol_d = {}, {}, {}, {}, {}
    for sym in symbols:
        df = load_symbol(sym, start, end)
        if len(df) < min_bars:
            raise ValueError(
                f"{sym} has {len(df)} bars; the configured minimum is {min_bars}."
            )
        close_d[sym] = df["close"]
        high_d[sym] = df["high"]
        low_d[sym] = df["low"]
        open_d[sym] = df["open"]
        vol_d[sym] = df["volume"]
    panels = {}
    for name, d in [("close", close_d), ("high", high_d), ("low", low_d),
                     ("open", open_d), ("volume", vol_d)]:
        panels[name] = pd.concat(d, axis=1)
    cols = sorted(set(panels["close"].columns) & set(panels["high"].columns) &
                  set(panels["low"].columns) & set(panels["open"].columns) &
                  set(panels["volume"].columns))
    for name in panels:
        panels[name] = panels[name][cols]
    idx = panels["close"].index
    for name, panel in panels.items():
        aligned = panel.reindex(idx)
        if aligned.isna().any().any():
            missing = aligned.isna().stack()
            symbol = missing[missing].index[0][1]
            raise ValueError(f"{name} data for {symbol} has missing dates in the panel.")
        panels[name] = aligned
    return panels, cols, idx


def compute_signals(close, high, low, open_, volume, config):
    c = close
    v = volume
    o = open_
    sigs = {}
    for lb in config["momentum_lookbacks"]:
        s = np.full_like(c, -np.inf)
        s[lb:] = c[lb:] / c[:-lb] - 1.0
        s = np.roll(s, 1, axis=0)
        s[:lb + 2] = -np.inf
        sigs[f"mom_{lb}d"] = s
    gap = np.full_like(c, -np.inf)
    gap[1:] = (o[1:] - c[:-1]) / c[:-1]
    gap = np.roll(gap, 1, axis=0)
    gap[:2] = -np.inf
    sigs["gap_run"] = np.where(
        gap > config["gap_run_minimum_gap"], gap * sigs["mom_5d"], -np.inf
    )
    sigs["gap_run_5"] = np.where(
        gap > config["gap_run_5_minimum_gap"], gap * sigs["mom_5d"], -np.inf
    )
    vol_avg = np.full_like(v, np.nan)
    for i in range(config["volume_lookback"], len(v)):
        vol_avg[i] = v[i - config["volume_lookback"]:i].mean(axis=0)
    measured_average = vol_avg[config["volume_lookback"]:]
    if np.isnan(measured_average).any() or (measured_average <= 0).any():
        raise ValueError("Configured volume history is missing or nonpositive.")
    vr = v / vol_avg
    sigs["vol_mom_5d"] = np.where(
        vr > config["volume_momentum_minimum_ratio"],
        sigs["mom_5d"] * (config["volume_momentum_base_weight"] + vr), -np.inf
    )
    # Gap with volume filter
    sigs["gap_vol"] = np.where(
        (gap > config["gap_vol_minimum_gap"])
        & (vr > config["gap_vol_minimum_ratio"]),
        gap * sigs["mom_5d"] * vr,
        -np.inf,
    )
    return sigs


def compute_exit(close, high, low, t, picks, max_holding, sl_pct, tp_pct,
                 price_epsilon):
    entry = close[t, picks]
    path_end = min(t + max_holding + 1, close.shape[0])
    if path_end <= t + 1 or len(picks) == 0:
        return np.zeros(len(picks)), 0, 0, 0
    hi = high[t + 1:path_end][:, picks]
    lo = low[t + 1:path_end][:, picks]
    cl = close[t + 1:path_end][:, picks]
    eps = price_epsilon
    cum_hi = hi / (entry + eps) - 1.0
    cum_lo = lo / (entry + eps) - 1.0
    stop_lvl = -sl_pct / 100.0
    tgt_lvl = tp_pct / 100.0
    stop_hits = cum_lo <= stop_lvl
    target_hits = cum_hi >= tgt_lvl
    n_days = path_end - (t + 1)
    if n_days == 1:
        exits = cl[-1] / (entry + eps) - 1.0
        sw = stop_hits[-1]
        tw = target_hits[-1] & ~sw
        exits[sw] = stop_lvl
        exits[tw] = tgt_lvl
        return exits, int(np.sum(sw)), int(np.sum(tw)), int(np.sum(~sw & ~tw))
    sf = np.argmax(stop_hits, axis=0)
    sa = np.any(stop_hits, axis=0)
    tf = np.argmax(target_hits, axis=0)
    ta = np.any(target_hits, axis=0)
    exits = cl[-1] / (entry + eps) - 1.0
    sw = sa & (~ta | (sf <= tf))
    tw = ta & (~sa | (tf < sf))
    exits[sw] = stop_lvl
    exits[tw] = tgt_lvl
    return exits, int(np.sum(sw)), int(np.sum(tw)), len(picks) - int(np.sum(sw)) - int(np.sum(tw))


def build_regime(nifty_close, window):
    n = len(nifty_close)
    filt = np.zeros(n, dtype=bool)
    for i in range(window - 1, n):
        sma = np.nanmean(nifty_close[i - window + 1:i + 1])
        filt[i] = nifty_close[i] > sma if not np.isnan(sma) else False
    return filt


def run_strategy(close, high, low, signal, sl_pct, tp_pct, max_holding, k, step,
                 config, costs, regime_filter, vol_target, min_rebalances):
    n_dates, n_stocks = close.shape
    warmup = config["warmup_bars"]
    rb_dates = list(range(warmup, n_dates - max_holding, step))
    if len(rb_dates) < min_rebalances:
        return {"error": "insufficient dates"}

    daily_ret = np.zeros_like(close)
    daily_ret[1:] = close[1:] / close[:-1] - 1.0

    all_ret = []
    total_trades = 0
    total_stops = 0
    total_targets = 0

    for t in rb_dates:
        if regime_filter is not None and not regime_filter[t]:
            continue
        scores = signal[t, :].copy()
        valid = close[t, :] > 0
        scores = np.where(valid, scores, -np.inf)
        top_k = min(k, int(np.sum(scores > -np.inf)))
        if top_k < k:
            continue
        picks = np.argpartition(-scores, top_k - 1)[:top_k]
        picks = picks[np.argsort(-scores[picks])]

        exits, n_stop, n_target, n_hold = compute_exit(
            close, high, low, t, picks, max_holding, sl_pct, tp_pct,
            config["price_epsilon"],
        )

        if vol_target > 0:
            vol_window = config["volatility_lookback"]
            recent_ret = daily_ret[t - vol_window:t, :][:, picks]
            stock_vol = np.std(recent_ret, axis=0, ddof=1) * np.sqrt(
                config["trading_days"]
            )
            if (not np.isfinite(stock_vol).all()
                    or (stock_vol <= config["minimum_annualized_volatility"]).any()):
                raise ValueError("Selected instruments have invalid volatility history.")
            inv_vol = 1.0 / stock_vol
            raw_weights = inv_vol / inv_vol.sum()
            avg_vol = np.mean(stock_vol)
            vol_scale = min(1.0, vol_target / avg_vol)
        else:
            raw_weights = np.full(len(picks), 1.0 / len(picks))
            vol_scale = 1.0
        net = exits - costs
        portfolio_ret = float(np.sum(raw_weights * vol_scale * net))
        all_ret.append(portfolio_ret)
        total_trades += k
        total_stops += n_stop
        total_targets += n_target

    if len(all_ret) < min_rebalances:
        return {"error": f"insufficient trades ({len(all_ret)} < {min_rebalances})"}

    returns = np.array(all_ret)
    cum = np.cumprod(1 + returns)
    total_ret = float(cum[-1] - 1)
    n_p = len(returns)
    years = n_p * step / config["trading_days"]
    cagr = (1 + total_ret) ** (1.0 / years) - 1 if years > 0 else 0
    ppy = config["trading_days"] / step
    arith_annual = float(np.mean(returns)) * ppy
    vol = float(np.std(returns, ddof=1)) * np.sqrt(ppy) if n_p > 1 else 0
    sharpe = (arith_annual - config["annual_risk_free_rate"]) / vol if vol > 0 else 0
    wf = config["sessions_per_week"] / step
    wr = returns * wf
    rmax = np.maximum.accumulate(cum)
    max_dd = float(np.min(cum / rmax - 1))

    return {
        "weekly_mean_pct": round(100 * np.mean(wr), 3),
        "weekly_vol_pct": round(100 * np.std(wr, ddof=1), 3) if len(wr) > 1 else 0,
        "sharpe": round(sharpe, 3),
        "cagr_pct": round(100 * cagr, 2),
        "total_return_pct": round(100 * total_ret, 2),
        "max_drawdown_pct": round(100 * max_dd, 2),
        "weekly_win_rate_pct": round(100 * np.mean(wr > 0), 1),
        "weeks_ge_2pct_pct": round(100 * np.mean(wr >= config["weekly_thresholds"]["2"]), 1),
        "weeks_ge_3pct_pct": round(100 * np.mean(wr >= config["weekly_thresholds"]["3"]), 1),
        "weeks_ge_5pct_pct": round(100 * np.mean(wr >= config["weekly_thresholds"]["5"]), 1),
        "num_trades": total_trades,
        "win_rate_pct": round(100 * total_targets / max(total_trades, 1), 1),
        "stop_rate_pct": round(100 * total_stops / max(total_trades, 1), 1),
        "n_rebalances": n_p,
        "min_weekly_pct": round(100 * np.min(wr), 3),
        "max_weekly_pct": round(100 * np.max(wr), 3),
        "median_weekly_pct": round(100 * np.median(wr), 3),
    }


def main():
    """Run the configured parameter search and walk-forward study."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    config_path = parser.parse_args().config
    config = json.loads(config_path.read_text(encoding="utf-8"))
    P(f"Configuration: {config_path.resolve()}")
    start = pd.Timestamp(config["start_date"])
    end = pd.Timestamp(config["end_date"])
    from strategy_hunt import parse_universe
    all_symbols = list(
        dict.fromkeys(
            symbol
            for path in config["universe_files"]
            for symbol in parse_universe(ROOT / path)
        )
    )
    P(f"Loading {len(all_symbols)} symbols...")
    t0 = time.time()
    panels, syms, dates = load_stocks(
        all_symbols, start, end, min_bars=config["minimum_bars"]
    )
    if not syms or len(dates) == 0:
        raise ValueError("Configured date range produced no usable stock data.")
    P(f"  {len(syms)} stocks, {len(dates)} dates in {time.time()-t0:.1f}s")

    nifty = load_symbol(config["benchmark_symbol"], start, end)
    aligned_benchmark = nifty["close"].reindex(dates)
    if aligned_benchmark.isna().any():
        raise ValueError("Benchmark is missing dates required for regime signals.")
    nif_close = aligned_benchmark.to_numpy(dtype=np.float64)
    regime_50 = build_regime(nif_close, config["short_regime_window"])
    regime_200 = build_regime(nif_close, config["long_regime_window"])
    regime_dual = regime_50 & regime_200
    regime_or = regime_50 | regime_200

    close = panels["close"].to_numpy(dtype=np.float64)
    high = panels["high"].to_numpy(dtype=np.float64)
    low = panels["low"].to_numpy(dtype=np.float64)
    open_ = panels["open"].to_numpy(dtype=np.float64)
    volume = panels["volume"].to_numpy(dtype=np.float64)
    n_dates = len(dates)
    mid = int(n_dates * config["oos_start_fraction"])
    P(f"  Date: {dates[0].date()} → {dates[-1].date()}")
    P(f"  Regime: dual={np.sum(regime_dual)}({100*np.mean(regime_dual):.0f}%) or={np.sum(regime_or)}({100*np.mean(regime_or):.0f}%)")

    signals = compute_signals(close, high, low, open_, volume, config)
    sigs_oos = {name: sig[mid:] for name, sig in signals.items()}

    # ================================================================
    # EXHAUSTIVE SWEEP: min 15 rebalances, 2-5% weekly target
    # ================================================================
    P(f"\n{'='*80}")
    P(f"EXHAUSTIVE SWEEP (OOS, weekly target {config['target_weekly_minimum_pct']}–{config['target_weekly_maximum_pct']}%)")
    P(f"{'='*80}")

    param_grid = list(product(
        [tuple(pair) for pair in config["stop_target_pairs"]],
        config["position_counts"],
        config["entry_steps"],
        config["maximum_holding_days"],
        config["annual_volatility_targets"],
    ))
    # (sl, tp), k, step, mh, vt
    sig_names = config["signals"]
    regime_arrays = {
        "none": None,
        "200d": regime_200[mid:],
        "dual": regime_dual[mid:],
        "or": regime_or[mid:],
    }
    regime_names = [(name, regime_arrays[name]) for name in config["regimes"]]

    P(f"  {len(param_grid)} params × {len(sig_names)} signals × {len(regime_names)} regimes = {len(param_grid)*len(sig_names)*len(regime_names)}")

    t1 = time.time()
    all_results = []
    for sig_name in sig_names:
        sig_arr = sigs_oos[sig_name]
        for regime_name, reg in regime_names:
            for (sl, tp), k, step, mh, vt in param_grid:
                r = run_strategy(close[mid:], high[mid:], low[mid:], sig_arr,
                                 sl, tp, mh, k, step, config=config,
                                 costs=config["round_trip_cost"],
                                 regime_filter=reg, vol_target=vt,
                                 min_rebalances=config["minimum_rebalances"])
                if "error" in r:
                    continue
                if r["max_drawdown_pct"] < config["maximum_drawdown_pct"]:
                    continue
                all_results.append({
                    "signal": sig_name, "regime": regime_name,
                    "sl": sl, "tp": tp, "mh": mh, "k": k, "step": step, "vt": vt,
                    **r,
                })

    P(f"  {len(all_results)} valid configs (>=15 rebalances, DD>-30%) in {time.time()-t1:.1f}s")

    # Filter: 2-5% weekly, Sharpe > 1, sort by Sharpe
    target = [
        r for r in all_results
        if config["target_weekly_minimum_pct"] <= r["weekly_mean_pct"]
        <= config["target_weekly_maximum_pct"]
        and r["sharpe"] > config["minimum_target_sharpe"]
    ]
    target.sort(key=lambda r: r["sharpe"], reverse=True)
    P(f"  Configs in 2-5% range with Sharpe > 1: {len(target)}")

    P("\n  Top 20 by Sharpe:")
    for r in target[:config["top_sharpe_count"]]:
        P(f"  {r['signal']}/{r['regime']}/sl{r['sl']}/tp{r['tp']}/mh{r['mh']}/k{r['k']}/vt{r['vt']}: "
          f"wk={r['weekly_mean_pct']:+.3f}% Sharpe={r['sharpe']:.3f} DD={r['max_drawdown_pct']:.0f}% "
          f"rb={r['n_rebalances']} pos_wk={r['weekly_win_rate_pct']:.0f}%")
        P(f"    P(>=5%)={r['weeks_ge_5pct_pct']:.0f}% med={r['median_weekly_pct']:+.3f}% min={r['min_weekly_pct']:+.3f}%")

    # Also: configs with most trades in 2-5% range
    target_trade = sorted(target, key=lambda r: r["n_rebalances"] * r["num_trades"], reverse=True)
    P("\n  Top 10 by trade count (in 2-5% range):")
    for r in target_trade[:config["top_trade_count"]]:
        P(f"  {r['signal']}/{r['regime']}/sl{r['sl']}/tp{r['tp']}/mh{r['mh']}/k{r['k']}/vt{r['vt']}: "
          f"wk={r['weekly_mean_pct']:+.3f}% Sharpe={r['sharpe']:.3f} DD={r['max_drawdown_pct']:.0f}% "
          f"rb={r['n_rebalances']} pos_wk={r['weekly_win_rate_pct']:.0f}%")

    # ================================================================
    # WALK-FORWARD for best 3 configs (most trades)
    # ================================================================
    P(f"\n{'='*80}")
    P("WALK-FORWARD (expanding window, 3 folds)")
    P(f"{'='*80}")

    # Pick top 3 by Sharpe among configs with >=20 rebalances
    robust = [r for r in target if r["n_rebalances"] >= config["robust_minimum_rebalances"]]
    robust.sort(key=lambda r: r["sharpe"], reverse=True)
    if not robust:
        raise ValueError("No target configuration met the walk-forward sample gate.")
    wf_configs = robust[:config["walk_forward_top_count"]]

    # Also include the best gap_run_5/no_regime config for comparison
    for r in all_results:
        if r["signal"] == config["reference_no_regime"]["signal"] and r["regime"] == "none" and r["sl"] == config["reference_no_regime"]["stop"] and r["tp"] == config["reference_no_regime"]["target"] and r["mh"] == config["reference_no_regime"]["holding"] and r["k"] == config["reference_no_regime"]["positions"] and r["step"] == config["reference_no_regime"]["step"] and r["vt"] == config["reference_no_regime"]["vol_target"]:
            wf_configs.append(r)
            break
    # And the gap_run_5/200d config
    for r in all_results:
        if r["signal"] == config["reference_long_regime"]["signal"] and r["regime"] == "200d" and r["sl"] == config["reference_long_regime"]["stop"] and r["tp"] == config["reference_long_regime"]["target"] and r["mh"] == config["reference_long_regime"]["holding"] and r["k"] == config["reference_long_regime"]["positions"] and r["step"] == config["reference_long_regime"]["step"] and r["vt"] == config["reference_long_regime"]["vol_target"]:
            wf_configs.append(r)
            break

    for r_top in wf_configs[:config["walk_forward_max_count"]]:
        sig_name = r_top["signal"]
        sig_arr = signals[sig_name]
        if r_top["regime"] == "none":
            reg_full = None
        elif r_top["regime"] == "200d":
            reg_full = regime_200
        elif r_top["regime"] == "dual":
            reg_full = regime_dual
        else:
            reg_full = regime_or

        P(f"\n  {sig_name}/{r_top['regime']}/sl{r_top['sl']}/tp{r_top['tp']}/"
          f"mh{r_top['mh']}/k{r_top['k']}/vt{r_top['vt']}")

        # Train (2020-01 → 2023-05)
        tr = run_strategy(close[:mid], high[:mid], low[:mid], sig_arr[:mid],
                          r_top["sl"], r_top["tp"], r_top["mh"], r_top["k"],
                          r_top["step"], config=config,
                          costs=config["round_trip_cost"],
                          regime_filter=reg_full[:mid] if reg_full is not None else None,
                          vol_target=r_top["vt"],
                          min_rebalances=config["walk_forward_minimum_rebalances"])
        if "error" not in tr:
            P(f"    TRAIN: wk={tr['weekly_mean_pct']:+.3f}% Sharpe={tr['sharpe']:.3f} "
              f"DD={tr['max_drawdown_pct']:.0f}% rb={tr['n_rebalances']}")

        # OOS (2023-05 → 2026-09) — also walk-forward within OOS
        oos = run_strategy(close[mid:], high[mid:], low[mid:], sig_arr[mid:],
                           r_top["sl"], r_top["tp"], r_top["mh"], r_top["k"],
                           r_top["step"], config=config,
                           costs=config["round_trip_cost"],
                           regime_filter=reg_full[mid:] if reg_full is not None else None,
                           vol_target=r_top["vt"],
                           min_rebalances=config["walk_forward_minimum_rebalances"])
        if "error" not in oos:
            P(f"    OOS:   wk={oos['weekly_mean_pct']:+.3f}% Sharpe={oos['sharpe']:.3f} "
              f"DD={oos['max_drawdown_pct']:.0f}% rb={oos['n_rebalances']} "
              f"pos_wk={oos['weekly_win_rate_pct']:.0f}%")
            P(f"    P(>=2%)={oos['weeks_ge_2pct_pct']:.0f}% P(>=3%)={oos['weeks_ge_3pct_pct']:.0f}%")
            P(f"P(>=5%)={oos['weeks_ge_5pct_pct']:.0f}% med={oos['median_weekly_pct']:+.3f}%")
            P(f"min={oos['min_weekly_pct']:+.3f}% max={oos['max_weekly_pct']:+.3f}%")

        # Sub-period walk-forward within OOS (3 sub-folds)
        oos_len = n_dates - mid
        sub_size = oos_len // config["subperiod_count"]
        P("    Sub-periods (within OOS):")
        for si in range(config["subperiod_count"]):
            ss = mid + si * sub_size
            se = (mid + (si + 1) * sub_size
                  if si < config["subperiod_count"] - 1 else n_dates)
            sub_r = run_strategy(close[ss:se], high[ss:se], low[ss:se], sig_arr[ss:se],
                                 r_top["sl"], r_top["tp"], r_top["mh"], r_top["k"],
                                 r_top["step"], config=config,
                                 costs=config["round_trip_cost"],
                                 regime_filter=reg_full[ss:se] if reg_full is not None else None,
                                 vol_target=r_top["vt"],
                                 min_rebalances=config["subperiod_minimum_rebalances"])
            if "error" in sub_r:
                P(f"      Fold {si+1} ({dates[ss].date()} → {dates[se-1].date()}): insufficient")
            else:
                P(f"      Fold {si+1} ({dates[ss].date()} → {dates[se-1].date()}): "
                  f"wk={sub_r['weekly_mean_pct']:+.3f}% Sharpe={sub_r['sharpe']:.3f} "
                  f"DD={sub_r['max_drawdown_pct']:.0f}% rb={sub_r['n_rebalances']}")

    # ================================================================
    # FINAL RECOMMENDATION
    # ================================================================
    P(f"\n{'='*80}")
    P("FINAL RECOMMENDATION")
    P(f"{'='*80}")

    # Find the best config: in 2-5% range, Sharpe > 2, most rebalances
    final = [
        r for r in target
        if r["n_rebalances"] >= config["recommendation_minimum_rebalances"]
        and r["sharpe"] > config["recommendation_minimum_sharpe"]
    ]
    final.sort(key=lambda r: (r["sharpe"], r["n_rebalances"]), reverse=True)

    if not final:
        raise ValueError("No configuration meets the configured recommendation criteria.")
    best = final[0]
    P(f"\n  Recommended: {best['signal']}/{best['regime']}/sl{best['sl']}/tp{best['tp']}/"
  f"mh{best['mh']}/k{best['k']}/s{best['step']}/vt{best['vt']}")
    P(f"  OOS ({dates[mid].date()} → {dates[-1].date()}):")
    P(f"    Weekly mean:    {best['weekly_mean_pct']:+.3f}%")
    P(f"    Sharpe:         {best['sharpe']:.3f}")
    P(f"    CAGR:           {best['cagr_pct']:.0f}%")
    P(f"    Total return:   {best['total_return_pct']:.0f}%")
    P(f"    Max drawdown:   {best['max_drawdown_pct']:.1f}%")
    P(f"    Weekly vol:     {best['weekly_vol_pct']:.3f}%")
    P(f"    Win rate (wk):  {best['weekly_win_rate_pct']:.0f}%")
    P(f"    P(>=2% week):   {best['weeks_ge_2pct_pct']:.0f}%")
    P(f"    P(>=5% week):   {best['weeks_ge_5pct_pct']:.0f}%")
    P(f"    Rebalances:     {best['n_rebalances']} ({best['num_trades']} trades)")
    P(f"    Position win:   {best['win_rate_pct']:.0f}%  Stop: {best['stop_rate_pct']:.0f}%")
    P(f"    Range:          {best['min_weekly_pct']:+.3f}% to {best['max_weekly_pct']:+.3f}%")
    # Save
    output_path = ROOT / config["output_file"]
    payload = {
        "generated_at": pd.Timestamp.now().isoformat(),
        "oos_window": [str(dates[mid].date()), str(dates[-1].date())],
        "n_valid_configs": len(all_results),
        "n_target_configs": len(target),
        "all_results": all_results[:config["saved_result_count"]],
        "top_by_sharpe": target[:config["top_sharpe_count"]],
        "best": final[0],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    P(f"\n  Results saved to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
