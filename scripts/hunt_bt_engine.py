"""BacktestEngine-based strategy: multi-stock momentum with stop-losses and take-profits.

Key difference from the matrix-based run_weights: this engine processes bars
sequentially and supports position-level exits via stop-loss/take-profit checks
against the daily high/low. This caps downside while locking in gains — the
return distribution is fundamentally different from a simple momentum rank.

Strategy: DailyMomentumStops
  - Rank stocks by N-day momentum each day
  - Enter top-K positions with fixed allocation
  - Stop-loss at X% below entry (checked against daily low)
  - Take-profit at Y% above entry (checked against daily high)
  - Max holding period of Z days (force exit)
  - Trend filter: only long when above N-day SMA
  - Cooldown after exit (no immediate re-entry)

Tests the full grid with walk-forward OOS validation.
"""
# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from itertools import product

import strategy_hunt as sh

from forward.backtest.engine import BacktestConfig, BacktestEngine
from forward.config.costs import indian_delivery_costs
from forward.core.costs import SlippageModel
from forward.core.enums import AssetClass, Timeframe
from forward.core.feeds import ListFeed
from forward.core.instrument_models import Instrument
from forward.data.base import pivot_to_snapshots
from forward.execution.risk import RiskLimits
from forward.strategy.base import Strategy, StrategyContext

p = print


def P(*a, **k):
    k.setdefault("flush", True)
    p(*a, **k)


def load_feed(
    symbols: list[str], start: pd.Timestamp, end: pd.Timestamp,
    settings: dict,
) -> tuple[ListFeed, dict, dict]:
    """Build a ListFeed from cached parquet daily data."""
    frames: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        path = ROOT / settings["daily_data_directory"] / f"{sym}.parquet"
        df = pd.read_parquet(
            path,
            columns=["ts", "open", "high", "low", "close", "volume"],
        )
        df = df.dropna(subset=["close"])
        df = df[df["close"] > 0]
        if len(df) < settings["minimum_history_bars"]:
            raise ValueError(
                f"{sym} has {len(df)} bars; required minimum is "
                f"{settings['minimum_history_bars']}."
            )
        df = df.set_index("ts")
        df.index = pd.DatetimeIndex(df.index)
        df = df.loc[(df.index >= start) & (df.index <= end)]
        if len(df) < settings["minimum_window_bars"]:
            raise ValueError(
                f"{sym} has {len(df)} bars in the configured window; required "
                f"minimum is {settings['minimum_window_bars']}."
            )
        frames[sym] = df

    if not frames:
        raise ValueError("no data loaded")

    # Build long-format DataFrame for pivot_to_snapshots
    long_rows = []
    for sym, frame in frames.items():
        for ts, row in frame.iterrows():
            long_rows.append(
                {
                    "ts": ts,
                    "symbol": sym,
                    "open": row["open"],
                    "high": row["high"],
                    "low": row["low"],
                    "close": row["close"],
                    "volume": row["volume"],
                }
            )
    long_df = pd.DataFrame(long_rows).sort_values("ts", kind="mergesort")

    instruments = {
        sym: Instrument(
            symbol=sym,
            exchange=settings["exchange"],
            asset_class=AssetClass(settings["asset_class"]),
        )
        for sym in frames
    }
    expected_dates = next(iter(frames.values())).index
    for symbol, frame in frames.items():
        if not frame.index.equals(expected_dates):
            raise ValueError(f"{symbol} has dates that do not align with the configured panel.")
    snapshots = pivot_to_snapshots(long_df, Timeframe(settings["timeframe"]))
    feed = ListFeed(snapshots, instruments)
    return feed, frames, instruments


class DailyMomentumStops(Strategy):
    """Multi-stock daily momentum with stop-loss / take-profit / holding period.

    On each day, for each held position, checks whether the daily low hit the
    stop-loss or the daily high hit the take-profit. Enters new long positions
    in the top-K momentum stocks that pass the trend filter.
    """

    name = "daily_momentum_stops"

    def __init__(self, **params):
        super().__init__(**params)
        self.lookback = params["lookback"]
        self.stop_loss_pct = params["stop_loss_pct"]
        self.take_profit_pct = params["take_profit_pct"]
        self.max_positions = params["max_positions"]
        self.allocation_pct = params["allocation_pct"]
        self.trend_sma = params["trend_sma"]
        self.max_holding_days = params["max_holding_days"]
        self.cooldown_bars = params["cooldown_bars"]
        self.minimum_quantity = params["minimum_quantity"]
        self._entry_price: dict[str, float] = {}
        self._peak: dict[str, float] = {}
        self._entry_date: dict[str, int] = {}
        self._cooldown: dict[str, int] = {}
        self._all_mom: pd.DataFrame | None = None

    def prepare(self, frames: dict[str, pd.DataFrame]) -> None:
        """Pre-compute momentum and trend filter for all symbols."""
        all_mom = {}
        all_trend = {}
        for sym, frame in frames.items():
            close = frame["close"]
            all_mom[sym] = close / close.shift(self.lookback) - 1.0
            if self.trend_sma:
                all_trend[sym] = close.rolling(self.trend_sma, min_periods=self.trend_sma).mean()
        # Build a combined DataFrame indexed by date
        mom_df = pd.concat(all_mom, axis=1)
        self._all_mom = mom_df
        self._trend_df = pd.concat(all_trend, axis=1) if all_trend else None

    def on_start(self, ctx: StrategyContext) -> None:
        """Store prepared indicator DataFrames for fast lookup."""
        pass

    def on_bar(self, ctx: StrategyContext) -> None:
        ts = ctx.now
        idx = ctx.index

        # --- 1. Manage existing positions: check stops, targets, holding period ---
        for symbol in list(self._entry_price.keys()):
            if symbol not in ctx.instruments:
                continue
            row = ctx.row(symbol)
            close = row["close"]
            high = row["high"]
            low = row["low"]
            if not close or pd.isna(close):
                continue

            entry = self._entry_price[symbol]
            peak = self._peak[symbol]
            peak = max(peak, high)

            stop_price = entry * (1 - self.stop_loss_pct / 100.0)
            target_price = entry * (1 + self.take_profit_pct / 100.0)

            # Check stop-loss
            if low <= stop_price:
                ctx.target(symbol, 0, tag="exit-stop-loss")
                self._entry_price.pop(symbol, None)
                self._peak.pop(symbol, None)
                self._entry_date.pop(symbol, None)
                self._cooldown[symbol] = self.cooldown_bars
                continue

            # Check take-profit
            if high >= target_price:
                ctx.target(symbol, 0, tag="exit-take-profit")
                self._entry_price.pop(symbol, None)
                self._peak.pop(symbol, None)
                self._entry_date.pop(symbol, None)
                self._cooldown[symbol] = self.cooldown_bars
                continue

            # Check holding period
            holding = idx - self._entry_date[symbol]
            if holding >= self.max_holding_days:
                ctx.target(symbol, 0, tag="exit-max-hold")
                self._entry_price.pop(symbol, None)
                self._peak.pop(symbol, None)
                self._entry_date.pop(symbol, None)
                self._cooldown[symbol] = self.cooldown_bars
                continue

            self._peak[symbol] = peak

        # --- 2. Cooldown countdown ---
        for sym in list(self._cooldown.keys()):
            if self._cooldown[sym] > 0:
                self._cooldown[sym] -= 1

        # --- 3. Enter new positions ---
        # Update cooldown for symbols we're trying
        n_held = sum(1 for s in self._entry_price if ctx.has_position(s))
        n_can_enter = self.max_positions - n_held

        if n_can_enter <= 0:
            return

        # Rank by momentum (only consider symbols we don't already have
        # and that are past warmup)
        if self._all_mom is not None and ts in self._all_mom.index:
            mom_series = self._all_mom.loc[ts]
            # Trend filter
            if self._trend_df is not None and ts in self._trend_df.index:
                trend_series = self._trend_df.loc[ts]
            else:
                trend_series = None

            candidates = []
            for sym in ctx.instruments:
                if sym in self._entry_price:
                    continue
                if self._cooldown.get(sym, 0) > 0:
                    continue
                if sym not in ctx.instruments:
                    continue
                row = ctx.row(sym)
                close = row["close"]
                if not close or pd.isna(close):
                    continue
                # Check we have position data
                pos = ctx.position(sym)
                if pos.quantity != 0:
                    continue

                mom_val = mom_series.get(sym, np.nan)
                if pd.isna(mom_val):
                    continue

                # Trend filter: close must be above trend SMA
                if trend_series is not None:
                    tr = trend_series.get(sym, np.nan)
                    if pd.isna(tr) or close < tr:
                        continue

                candidates.append((sym, mom_val, close))

            # Sort by momentum descending, take top K
            candidates.sort(key=lambda x: x[1], reverse=True)
            for sym, _, close in candidates[:n_can_enter]:
                # Position size
                target_qty = self._size(ctx, sym, close)
                if target_qty > 0:
                    ctx.target(sym, target_qty, tag="momentum-entry")
                    self._entry_price[sym] = close
                    self._peak[sym] = close
                    self._entry_date[sym] = idx
                    self._cooldown.pop(sym, None)

    def on_stop(self, ctx: StrategyContext) -> None:
        ctx.close_all()

    def on_session_end(self, ctx: StrategyContext) -> None:
        """Square off at end of day if configured."""
        pass

    def _size(self, ctx: StrategyContext, symbol: str, price: float) -> int:
        """Calculate position size based on allocation_pct of equity."""
        equity = ctx.equity
        if equity <= 0 or price <= 0:
            return 0
        target_value = equity * self.allocation_pct
        qty = max(self.minimum_quantity, int(round(target_value / price)))
        return qty


# ---------------------------------------------------------------------------
# Walk-forward test
# ---------------------------------------------------------------------------
def run_bt(
    frames: dict[str, pd.DataFrame],
    instruments: dict,
    symbols: list[str],
    start: pd.Timestamp,
    end: pd.Timestamp,
    params: dict,
    settings: dict,
) -> dict:
    """Build feed and run BacktestEngine with given params."""
    # Filter frames to window
    filtered = {
        s: f.loc[(f.index >= start) & (f.index <= end)]
        for s, f in frames.items()
        if len(f.loc[(f.index >= start) & (f.index <= end)])
        >= settings["minimum_window_bars"]
    }
    if set(filtered) != set(frames):
        missing_symbols = sorted(set(frames) - set(filtered))
        raise ValueError(f"Symbols lack required bars in the configured window: {missing_symbols}")

    instruments_f = {s: instruments[s] for s in filtered}
    long_rows = []
    for sym, frame in filtered.items():
        for ts, row in frame.iterrows():
            long_rows.append(
                {
                    "ts": ts,
                    "symbol": sym,
                    "open": row["open"],
                    "high": row["high"],
                    "low": row["low"],
                    "close": row["close"],
                    "volume": row["volume"],
                }
            )
    long_df = pd.DataFrame(long_rows).sort_values("ts", kind="mergesort")
    snapshots = pivot_to_snapshots(long_df, Timeframe(settings["timeframe"]))
    feed = ListFeed(snapshots, instruments_f)

    strategy = DailyMomentumStops(**params)

    # Warmup: lookback + trend_sma + 10 bars
    warmup = max(params["lookback"], params["trend_sma"]) + settings["warmup_extra_bars"]

    engine_config = BacktestConfig(
        initial_cash=settings["initial_cash"],
        commission=indian_delivery_costs(),
        slippage=SlippageModel(bps=settings["slippage_basis_points"]),
        fill_on_next_open=settings["fill_on_next_open"],
        allow_short=settings["allow_short"],
        square_off_eod=settings["square_off_eod"],
        risk_free_rate=settings["annual_risk_free_rate"],
        warmup_bars=warmup,
        risk=RiskLimits(
            max_daily_loss=(
                settings["initial_cash"]
                * settings["maximum_daily_loss_fraction"]
            )
        ),
        assert_invariants=settings["assert_invariants"],
    )

    engine = BacktestEngine(feed, strategy, engine_config)
    result = engine.run()

    m = result.metrics.as_dict()
    # Compute weekly returns
    equity = result.equity
    if len(equity) > 1:
        weekly = equity.resample(settings["weekly_resample_frequency"]).last().pct_change().dropna()
        if weekly.empty:
            raise ValueError("Backtest has no weekly returns in the configured window.")
        weekly_mean = round(100 * float(weekly.mean()), settings["display_decimal_places"])
        weekly_win = round(100 * float((weekly > 0).mean()), settings["display_decimal_places"])
        weekly_p_ge2 = round(
            100 * float((weekly >= settings["weekly_thresholds"]["2"]).mean()),
            settings["display_decimal_places"],
        )
        weekly_p_ge5 = round(
            100 * float((weekly >= settings["weekly_thresholds"]["5"]).mean()),
            settings["display_decimal_places"],
        )
    else:
        raise ValueError("Backtest produced no equity observations.")

    # Trade analysis
    trades = result.trades
    n_trades = len(trades)
    if n_trades > 0 and "net_pnl" in trades.columns:
        win_trades = trades[trades["net_pnl"] > 0]
        win_rate = round(100 * len(win_trades) / n_trades, settings["display_decimal_places"])
        avg_win = (
            round(float(win_trades["net_pnl"].mean() / settings["initial_cash"] * 100),
                  settings["display_decimal_places"])
            if len(win_trades) > 0
            else 0.0
        )
        avg_loss = (
            round(float(trades[trades["net_pnl"] <= 0]["net_pnl"].mean()
                        / settings["initial_cash"] * 100), settings["display_decimal_places"])
            if len(trades[trades["net_pnl"] <= 0]) > 0
            else 0.0
        )
    else:
        raise ValueError("Backtest produced no trades or omitted net P&L.")

    return {
        "cagr_pct": m["cagr_pct"],
        "sharpe": m["sharpe"],
        "max_dd_pct": m["max_drawdown_pct"],
        "total_return_pct": m["total_return_pct"],
        "num_trades": n_trades,
        "win_rate_pct": win_rate,
        "avg_win_pct": avg_win,
        "avg_loss_pct": avg_loss,
        "weekly_mean_pct": weekly_mean,
        "weekly_win_rate_pct": weekly_win,
        "weekly_p_ge_2pct": weekly_p_ge2,
        "weekly_p_ge_5pct": weekly_p_ge5,
        "commission": m["total_commission"],
        "slippage": m["total_slippage"],
        "killed": result.killed,
    }


def main():
    """Run the configured BacktestEngine parameter grid."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    config_path = parser.parse_args().config
    config = json.loads(config_path.read_text(encoding="utf-8"))
    P(f"Configuration: {config_path.resolve()}")
    start = pd.Timestamp(config["start_date"])
    end = pd.Timestamp(config["end_date"])

    symbols = list(
        dict.fromkeys(
            symbol
            for path in config["universe_files"]
            for symbol in sh.parse_universe(ROOT / path)
        )
    )
    if not symbols:
        raise ValueError("Configured universe files contain no symbols.")
    P(f"Loading data: {len(symbols)} symbols...")

    # Load data
    feed, frames, instruments = load_feed(symbols, start, end, config)
    P(f"  Loaded {len(frames)} stocks, {len(frames[list(frames.keys())[0]])} bars")

    # Walk-forward split
    all_index = sorted(frames.keys())
    first_sym = all_index[0]
    panel_dates = frames[first_sym].index
    mid = int(len(panel_dates) * config["oos_start_fraction"])
    train_end = panel_dates[mid]
    oos_start = panel_dates[mid + 1]

    P(f"\n  Train: {start.date()} → {train_end.date()}")
    P(f"  OOS:   {oos_start.date()} → {end.date()}")

    # Benchmark: buy & hold equal-weight
    bh_panel = pd.concat([frames[s]["close"] for s in frames], axis=1)
    bh_panel.columns = frames.keys()
    bh_panel = bh_panel.loc[(bh_panel.index >= oos_start) & (bh_panel.index <= end)]
    bh = bh_panel.mean(axis=1).pct_change().dropna()
    bh_weekly = (1 + bh).resample(config["weekly_resample_frequency"]).prod() - 1
    if bh_weekly.empty:
        raise ValueError("Benchmark has no weekly returns in the configured OOS window.")
    bh_weekly_mean = round(
        100 * float(bh_weekly.mean()), config["display_decimal_places"]
    )
    bh_cagr = round(
        100 * ((1 + float(bh.mean()) * len(bh) / config["trading_days"])
               ** config["trading_days"] - 1),
        config["display_decimal_places"],
    )

    P(
        f"\n  B&H (OOS, equal-weight): weekly_mean={bh_weekly_mean}% "
        f"weekly_win={100 * float((bh_weekly > 0).mean()):.{config['display_decimal_places']}f}%"
    )

    # Parameter grid — pre-registered, not tuned on results
    params_grid = list(
        product(
            config["lookbacks"],
            config["stop_loss_percentages"],
            config["take_profit_percentages"],
            config["position_counts"],
            config["trend_sma_windows"],
            config["maximum_holding_days"],
        )
    )
    extra_grid = list(
        product(
            config["no_trend_lookbacks"],
            config["no_trend_stop_loss_percentages"],
            config["no_trend_take_profit_percentages"],
            config["no_trend_position_counts"],
            config["no_trend_windows"],
            config["no_trend_maximum_holding_days"],
        )
    )
    params_grid.extend(extra_grid)
    P(f"\n  Testing {len(params_grid)} parameter combinations...")

    # Train on first half, validate on second half
    train_results = []
    for i, (lb, sl, tp, k, ts, mh) in enumerate(params_grid):
        if i % config["progress_interval"] == 0:
            P(f"  Train: {i}/{len(params_grid)}...")
        params = {
            "lookback": lb,
            "stop_loss_pct": sl,
            "take_profit_pct": tp,
            "max_positions": k,
            "allocation_pct": 1.0 / k,
            "trend_sma": ts,
            "max_holding_days": mh,
            "cooldown_bars": config["cooldown_bars"],
            "minimum_quantity": config["minimum_quantity"],
        }
        result = run_bt(frames, instruments, symbols, start, train_end, params, config)
        if result["num_trades"] < config["minimum_training_trades"]:
            continue
        result["params"] = {
            "lookback": lb,
            "stop_loss_pct": sl,
            "take_profit_pct": tp,
            "max_positions": k,
            "trend_sma": ts,
            "max_holding_days": mh,
        }
        train_results.append(result)

    P(f"\n  Train: {len(train_results)} valid configs")
    if not train_results:
        raise ValueError("No training configuration met the configured trade-count gate.")
    train_results.sort(key=lambda r: r["sharpe"], reverse=True)
    P("  Top configurations by Sharpe:")
    for r in train_results[:config["top_training_count"]]:
        params = r["params"]
        P(
            f"    lb={params['lookback']} sl={params['stop_loss_pct']} "
            f"tp={params['take_profit_pct']} k={params['max_positions']} "
            f"tsma={params['trend_sma']} mh={params['max_holding_days']}"
        )
        P(
            f"    Sharpe={r['sharpe']:.3f} CAGR={r['cagr_pct']:.1f}% "
            f"wk_mean={r['weekly_mean_pct']:+.3f}% DD={r['max_dd_pct']:.1f}% "
            f"trades={r['num_trades']} win={r['win_rate_pct']:.0f}% "
            f"avgW={r['avg_win_pct']:+.1f}% avgL={r['avg_loss_pct']:+.1f}%"
        )

    # Test top 10 train configs on OOS
    P(f"\n{'=' * 72}")
    P(f"WALK-FORWARD OOS VALIDATION (top {config['oos_validation_count']} from train)")
    P(f"{'=' * 72}")

    oos_results = []
    for r in train_results[:config["oos_validation_count"]]:
        params = r["params"]
        oos = run_bt(frames, instruments, symbols, oos_start, end, params, config)
        if oos["num_trades"] < config["minimum_oos_trades"]:
            continue
        oos["params"] = params
        oos["train"] = {
            key: r[key]
            for key in (
                "sharpe",
                "cagr_pct",
                "weekly_mean_pct",
                "max_dd_pct",
                "num_trades",
                "win_rate_pct",
                "avg_win_pct",
                "avg_loss_pct",
            )
        }
        oos_results.append(oos)
        P(
            f"\n  lb={params['lookback']} sl={params['stop_loss_pct']} "
            f"tp={params['take_profit_pct']} k={params['max_positions']} "
            f"tsma={params['trend_sma']} mh={params['max_holding_days']}"
        )
        P(
            f"    TRAIN: Sharpe={r['sharpe']:.3f} CAGR={r['cagr_pct']:.1f}% "
            f"wk={r['weekly_mean_pct']:+.3f}% win={r['win_rate_pct']:.0f}%"
        )
        P(
            f"    OOS:   Sharpe={oos['sharpe']:.3f} CAGR={oos['cagr_pct']:.1f}% "
            f"wk={oos['weekly_mean_pct']:+.3f}% win={oos['win_rate_pct']:.0f}% "
            f"trades={oos['num_trades']}"
        )
        P(
            f"    avgW={oos['avg_win_pct']:+.1f}% avgL={oos['avg_loss_pct']:+.1f}% "
            f"P(>=5%wk)={oos['weekly_p_ge_5pct']}% comm={oos['commission']:.0f}"
        )
    if not oos_results:
        raise ValueError("No OOS configuration met the configured trade-count gate.")

    # Summary
    max_train_wk = max((r["weekly_mean_pct"] for r in train_results), default=None)
    max_oos_wk = max((r["weekly_mean_pct"] for r in oos_results), default=None)
    max_train_sharpe = max((r["sharpe"] for r in train_results), default=None)
    max_oos_sharpe = max((r["sharpe"] for r in oos_results), default=None)

    P(f"\n{'=' * 72}")
    P("SUMMARY")
    P(f"{'=' * 72}")
    P(f"  Max train weekly mean: {max_train_wk}%")
    P(f"  Max OOS weekly mean:   {max_oos_wk}%")
    P(f"  Max train Sharpe:      {max_train_sharpe}")
    P(f"  Max OOS Sharpe:        {max_oos_sharpe}")
    P(f"  B&H weekly mean:       {bh_weekly_mean}%")

    payload = {
        "generated_at": pd.Timestamp.now().isoformat(),
        "train_window": [str(start.date()), str(train_end.date())],
        "oos_window": [str(oos_start.date()), str(end.date())],
        "benchmark": {
            "weekly_mean_pct": bh_weekly_mean,
            "cagr_pct": bh_cagr,
        },
        "n_train_configs": len(train_results),
        "max_train_weekly_mean": max_train_wk,
        "max_oos_weekly_mean": max_oos_wk,
        "max_train_sharpe": max_train_sharpe,
        "max_oos_sharpe": max_oos_sharpe,
        "top_train": train_results[:config["top_training_count"]],
        "oos_results": oos_results,
    }
    output_path = ROOT / config["output_file"]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    P(f"\n  Results saved to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
