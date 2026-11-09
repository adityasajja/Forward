"""Score the academic paper strategies out of sample, against two bars.

Why this exists
---------------
``forward.research.papers`` attaches a hardcoded ``win_rate`` (0.58, 0.64, ...) to
every signal and derives ``expected_value`` and ``confidence_score`` from it.
Those numbers are citations, not measurements. The walk-forward harness gates
every other strategy on prices it has never seen, but the paper models could
not be scored at all until ``paper_alpha.py`` adapted them — and no adapter had
been run.

This script runs the same harness over a real daily history and prints, per
strategy:

* the out-of-sample result (deflated Sharpe, versus buy-and-hold)
* a **random-selection control** — same universe, same cadence, same position
  sizing, but picks symbols at random. A signal that cannot beat its own
  control has demonstrated nothing beyond having been traded.
* the **measured** win rate, which is what should replace the hardcoded
  constant in papers.py.

Design note — why the feed is built here
----------------------------------------
``pivot_to_snapshots`` over the whole universe produces a *shared* time grid,
and ``BacktestEngine._build_frames`` then NaN-pads every symbol that did not
trade on a given date. For a 19-name universe over 6.5 years that means each
symbol spends most of its rows NaN, so ``ctx.history(symbol)`` hands the paper
models a frame whose ``close`` column is mostly missing and they never fire.

This validator requires complete OHLCV rows on the same observed dates for all
selected symbols. It rejects mismatched calendars instead of filling missing
bars. Fold arithmetic then counts a *symbol-bar* as a bar, which makes
``test_bars`` mean exactly what it says.

Usage::

    .venv/Scripts/python.exe scripts/validate_paper_strategies.py
    .venv/Scripts/python.exe scripts/validate_paper_strategies.py --strategies paper_iima_nse_momentum
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from forward.backtest.engine import BacktestConfig  # noqa: E402
from forward.core.costs import SlippageModel  # noqa: E402
from forward.core.enums import AssetClass  # noqa: E402
from forward.core.feeds import ListFeed  # noqa: E402
from forward.core.instrument_models import Instrument
from forward.core.market_models import Bar, MarketSnapshot
from forward.execution.risk import RiskLimits  # noqa: E402
from forward.research.data_hygiene import drop_reverting_spikes  # noqa: E402
from forward.research.validate import (  # noqa: E402
    ValidationConfig,
    WalkForwardConfig,
    walk_forward,
)
from forward.strategy.base import Strategy  # noqa: E402
from forward.strategy.strategies import STRATEGIES  # noqa: E402

CACHE_ROOT = Path(__file__).resolve().parents[1] / "data" / "iifl_daily" / "NSEEQ"
OHLCV = ("open", "high", "low", "close", "volume")

#: Default universe — liquid NSE large caps with a deep cached history. Reading
#: the whole 2,654-file cache to find the ~18 symbols that actually have years
#: of data wastes a minute per run and buries the result in "skip" lines.
DEFAULT_UNIVERSE = (
    "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN", "ITC", "LT",
    "AXISBANK", "KOTAKBANK", "BHARTIARTL", "HINDUNILVR", "MARUTI", "ASIANPAINT",
    "TITAN", "SUNPHARMA", "BAJFINANCE", "WIPRO", "TATAMOTORS", "HCLTECH",
    "ADANIENT", "NTPC", "POWERGRID", "ULTRACEMCO", "NESTLEIND", "BAJAJFINSV",
    "TECHM", "INDUSINDBK", "DRREDDY", "CIPLA",
)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def load_series(
    symbols: list[str], min_bars: int = 500
) -> dict[str, pd.DataFrame]:
    """Read cached parquets into per-symbol frames, each on its own timeline.

    Args:
        symbols: Instrument symbols to read.
        min_bars: Minimum usable bars required for a symbol.

    Returns:
        Valid market frames keyed by symbol.

    Raises:
        ValueError: If a selected cache has incomplete OHLCV data.
    """
    series: dict[str, pd.DataFrame] = {}
    dropped_total = 0
    for symbol in symbols:
        path = CACHE_ROOT / f"{symbol}.parquet"
        if not path.exists():
            print(f"  skip {symbol}: no cache")
            continue
        frame = pd.read_parquet(path)
        frame.columns = [c.strip().lower() for c in frame.columns]
        required = {"ts", *OHLCV}
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(f"{symbol} cache is missing columns: {sorted(missing)}")
        if len(frame) < min_bars:
            print(f"  skip {symbol}: {len(frame)} bars")
            continue
        frame = frame[["ts", *OHLCV]].copy()
        frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
        if frame.isna().any().any():
            raise ValueError(f"{symbol} cache has missing date or OHLCV values")
        if frame["ts"].duplicated().any():
            raise ValueError(f"{symbol} cache has duplicate dates")
        frame.loc[:, OHLCV] = frame.loc[:, OHLCV].apply(pd.to_numeric, errors="raise")
        values = frame.loc[:, OHLCV].to_numpy(dtype=float)
        if not np.isfinite(values).all():
            raise ValueError(f"{symbol} cache has non-finite OHLCV data")
        if (frame["volume"] < 0).any() or (frame[["open", "high", "low", "close"]] <= 0).any().any():
            raise ValueError(f"{symbol} cache has invalid OHLCV values")
        frame = frame.sort_values("ts").reset_index(drop=True)
        frame, dropped = drop_reverting_spikes(frame)
        if len(dropped):
            dropped_total += len(dropped)
            dates = ", ".join(
                f"{d:%Y-%m-%d}" for d in pd.to_datetime(dropped["ts"]).dt.date.unique()
            )
            print(f"  {symbol}: dropped {len(dropped)} corrupt bar(s) — {dates}")
        if len(frame) < min_bars:
            print(f"  skip {symbol}: {len(frame)} usable bars")
            continue
        series[symbol] = frame
    if dropped_total:
        print(f"  dropped {dropped_total} corrupt bar(s) in total")
    return series


def build_feed(series: dict[str, pd.DataFrame], exchange: str = "NSEEQ"):
    """Build snapshots only when every symbol has an observed bar.

    Args:
        series: Valid OHLCV frames keyed by symbol.
        exchange: Exchange assigned to each returned instrument.

    Returns:
        A feed and its instrument map.

    Raises:
        ValueError: If no symbols are provided or their date grids differ.
    """
    if not series:
        raise ValueError("paper validation needs at least one symbol")

    symbols = sorted(series)
    index = pd.DatetimeIndex(series[symbols[0]]["ts"])
    for symbol in symbols[1:]:
        symbol_dates = pd.DatetimeIndex(series[symbol]["ts"])
        if not index.equals(symbol_dates):
            raise ValueError(f"{symbol} does not have the same complete date grid")

    # Index once per symbol, not once per symbol per date. The obvious nesting
    # calls `set_index` 129 x 1,567 = 202k times on the mid-cap universe, which
    # is a minute of pure overhead for the same result.
    indexed = {s: f.set_index("ts") for s, f in series.items()}

    rows: list[MarketSnapshot] = []
    for ts in index:
        bars: dict[str, Bar] = {}
        for symbol, frame in indexed.items():
            r = frame.loc[ts]
            bars[symbol] = Bar(
                ts=ts.to_pydatetime(),
                open=float(r["open"]), high=float(r["high"]),
                low=float(r["low"]), close=float(r["close"]),
                volume=float(r["volume"]),
            )
        rows.append(MarketSnapshot(ts=ts.to_pydatetime(), bars=bars))

    instruments = {
        s: Instrument(symbol=s, exchange=exchange, asset_class=AssetClass.EQUITY)
        for s in series
    }
    return ListFeed(rows, instruments), instruments


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------


def measured_win_rate(trades: pd.DataFrame) -> tuple[float, int, float]:
    """Measure win rate and payoff expectancy from the canonical trade log.

    Args:
        trades: Closed trades with a ``net_pnl`` field.

    Returns:
        Win rate, number of trades, and payoff expectancy.

    Raises:
        ValueError: If the trade log lacks finite ``net_pnl`` values.
    """
    if trades.empty:
        return 0.0, 0, 0.0

    if "net_pnl" not in trades.columns:
        raise ValueError("trade log is missing the canonical net_pnl field")
    pnl = pd.to_numeric(trades["net_pnl"], errors="raise")
    if pnl.isna().any():
        raise ValueError("trade log has missing net_pnl values")
    wins = int((pnl > 0).sum())
    win_rate = wins / len(pnl)
    gains = float(pnl[pnl > 0].mean()) if wins else 0.0
    losses = float(abs(pnl[pnl <= 0].mean())) if (len(pnl) - wins) else 0.0
    payoff = (gains / losses) if losses > 0 else 0.0
    expectancy = (win_rate * payoff) - ((1.0 - win_rate) * 1.0)
    return round(win_rate, 4), int(len(pnl)), round(expectancy, 4)


def make_random_control(keep_pct: float, seed: int, allocation: float = 0.20):
    """A strategy that holds a random subset of the universe, re-drawn each bar.

    This is the null hypothesis made tradeable: same instruments, same sizing,
    same costs, same cadence — the only thing removed is the signal.
    """
    rng = random.Random(seed)

    class RandomSelectControl(Strategy):
        name = "random_control"
        alloc = allocation

        def on_bar(self, ctx) -> None:
            for symbol in ctx.instruments:
                if rng.random() < keep_pct:
                    close = float(ctx.row(symbol)["close"])
                    if close > 0 and close == close:
                        instrument = ctx.instruments[symbol]
                        notional = ctx.equity * self.alloc
                        step = max(getattr(instrument, "quantity_step", 1) or 1, 1)
                        qty = int(
                            int(notional / max(close * instrument.multiplier, 1e-9) / step) * step
                        )
                        ctx.target(symbol, qty, tag="random")
                        continue
                ctx.target(symbol, 0, tag="random-flat")

    return RandomSelectControl


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategies", default=",".join(sorted(
        s for s in STRATEGIES if s.startswith("paper_")
    )))
    ap.add_argument("--symbols", default=",".join(DEFAULT_UNIVERSE))
    ap.add_argument("--all-cached", action="store_true",
                    help="scan every cached parquet instead of the curated universe")
    ap.add_argument("--min-bars", type=int, default=1500,
                    help="only use symbols with at least this much cached history")
    ap.add_argument("--cash", type=float, default=1_000_000.0)
    ap.add_argument("--slippage-bps", type=float, default=5.0)
    ap.add_argument("--train", type=int, default=400,
                    help="training window in symbol-bars")
    ap.add_argument("--test", type=int, default=200,
                    help="out-of-sample window in symbol-bars")
    ap.add_argument("--warmup", type=int, default=260)
    ap.add_argument("--min-trades", type=int, default=30)
    ap.add_argument("--control-pct", type=float, default=0.20)
    ap.add_argument("--control-runs", type=int, default=8)
    ap.add_argument("--out", default="data/self_learning/paper_validation.json")
    args = ap.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if args.all_cached:
        symbols = sorted(p.stem for p in CACHE_ROOT.glob("*.parquet"))
    names = [s.strip() for s in args.strategies.split(",") if s.strip()]

    print(f"loading symbols from {CACHE_ROOT} (min {args.min_bars} bars) …")
    series = load_series(symbols, min_bars=args.min_bars)
    if not series:
        print("no symbols with enough history — run scripts/fetch_long_history.py", file=sys.stderr)
        return 1

    feed, instruments = build_feed(series)
    snaps = feed.load()
    span = f"{snaps[0].ts:%Y-%m-%d} → {snaps[-1].ts:%Y-%m-%d}"
    total_bars = sum(len(f) for f in series.values())
    print(f"  {len(series)} symbols, {total_bars:,} symbol-bars, {span}")
    for s, f in sorted(series.items()):
        print(f"    {s:<12} {len(f):>5} bars")
    print()

    config = WalkForwardConfig(
        train_bars=args.train, test_bars=args.test, warmup_bars=args.warmup
    )
    backtest = BacktestConfig(
        initial_cash=args.cash,
        slippage=SlippageModel(bps=args.slippage_bps),
        risk=RiskLimits(max_daily_loss=args.cash * 0.10),
    )
    validation = ValidationConfig(min_trades=args.min_trades)

    # --- control ------------------------------------------------------
    print(f"running {args.control_runs} random-selection control runs …")
    control_sharpes: list[float] = []
    control_returns: list[float] = []
    for run in range(args.control_runs):
        try:
            res = walk_forward(
                feed,
                make_random_control(args.control_pct, seed=1000 + run),
                {},
                config=config,
                backtest=backtest,
                validation=validation,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  run {run + 1} failed: {exc}")
            continue
        control_sharpes.append(res.oos_metrics.sharpe)
        control_returns.append(res.oos_metrics.total_return_pct)
        print(f"  run {run + 1}: Sharpe {res.oos_metrics.sharpe:+.2f}  "
              f"return {res.oos_metrics.total_return_pct:+.2f}%")

    ctrl_mean = statistics.fmean(control_sharpes) if control_sharpes else float("nan")
    ctrl_sd = statistics.stdev(control_sharpes) if len(control_sharpes) > 1 else 0.0
    print(f"  control mean Sharpe {ctrl_mean:+.3f} (sd {ctrl_sd:.3f}, "
          f"n={len(control_sharpes)})\n")

    # --- strategy -----------------------------------------------------
    results: list[dict] = []
    for name in names:
        cls = STRATEGIES.get(name)
        if cls is None:
            print(f"unknown strategy {name!r} — skipping")
            continue

        print(f"── {name} " + "─" * max(0, 58 - len(name)))
        try:
            res = walk_forward(
                feed, cls, {}, config=config, backtest=backtest, validation=validation
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  FAILED: {exc}\n")
            results.append({"strategy": name, "error": str(exc)})
            continue

        non_empty = [f.test_trades for f in res.folds if not f.test_trades.empty]
        trades = pd.concat(non_empty) if non_empty else pd.DataFrame()
        win_rate, n_trades, avg_r = measured_win_rate(trades)

        oos, bench = res.oos_metrics, res.benchmark_metrics
        z = ((oos.sharpe - ctrl_mean) / ctrl_sd) if ctrl_sd > 1e-9 else float("nan")

        print(res.verdict.summary())
        print(f"  OOS return          : {oos.total_return_pct:+.2f}% "
              f"(buy & hold {bench.total_return_pct:+.2f}%)")
        print(f"  OOS Sharpe          : {oos.sharpe:+.3f}  (buy & hold {bench.sharpe:+.3f})")
        print(f"  max drawdown        : {oos.max_drawdown_pct:.2f}%")
        print(f"  deflated Sharpe     : {res.deflated_sharpe:.3f} "
              f"vs hurdle {res.required_sharpe:.4f}  ({res.n_trials} trials)")
        print(f"  measured win rate   : {win_rate:.1%} over {n_trades} trades, "
              f"expectancy {avg_r:+.3f}R")
        print(f"  vs random control   : {z:+.2f}σ" if z == z else "  vs random control   : n/a (sd=0)")
        print()

        results.append({
            "strategy": name,
            "passed": bool(res.verdict.passed),
            "folds": len(res.folds),
            "oos_return_pct": round(oos.total_return_pct, 3),
            "oos_sharpe": round(oos.sharpe, 4),
            "oos_max_drawdown_pct": round(oos.max_drawdown_pct, 3),
            "oos_trades": int(oos.num_trades),
            "benchmark_return_pct": round(bench.total_return_pct, 3),
            "benchmark_sharpe": round(bench.sharpe, 4),
            "deflated_sharpe": round(res.deflated_sharpe, 4),
            "required_sharpe": round(res.required_sharpe, 4),
            "n_trials": res.n_trials,
            "measured_win_rate": win_rate,
            "measured_trades": n_trades,
            "measured_expectancy_r": avg_r,
            "sharpe_z_vs_control": None if z != z else round(z, 3),
            "checks": [{"name": c[0], "ok": bool(c[1]), "detail": c[2]} for c in res.verdict.checks],
        })

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "universe": sorted(series),
        "symbol_bars": total_bars,
        "window": span,
        "config": {
            "train_bars": args.train, "test_bars": args.test,
            "warmup_bars": args.warmup, "slippage_bps": args.slippage_bps,
            "initial_cash": args.cash, "min_trades": args.min_trades,
            "control_pct": args.control_pct, "control_runs": args.control_runs,
            "feed": "one symbol per snapshot, per-symbol timeline",
        },
        "control": {
            "sharpe_mean": None if ctrl_mean != ctrl_mean else round(ctrl_mean, 4),
            "sharpe_sd": round(ctrl_sd, 4),
            "sharpes": [round(s, 4) for s in control_sharpes],
            "returns_pct": [round(r, 3) for r in control_returns],
        },
        "results": results,
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf8")
    print(f"wrote {out_path}")

    scored = [r for r in results if "error" not in r]
    passed = [r for r in scored if r["passed"]]
    traded = [r for r in scored if r.get("oos_trades", 0) > 0]
    print(f"\n{len(passed)}/{len(scored)} passed every check; "
          f"{len(traded)}/{len(scored)} traded at all.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
