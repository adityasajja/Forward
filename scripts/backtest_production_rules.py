"""Run the PRODUCTION entry rules through walk-forward and print one table.

The point of this script is to answer "can we trade this?" with numbers rather
than a narrative. It runs `SignalEntryStrategy` -- the same class the live
scanner and the backtester both use -- over real daily history, and reports for
each fold split:

  * the out-of-sample return and Sharpe (never in-sample)
  * buy & hold on the identical window and universe
  * the multiple-testing-corrected probability the edge is real
  * the verdict the harness reaches

Run:
    ./.venv/Scripts/python.exe scripts/backtest_production_rules.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from forward.backtest.engine import BacktestConfig  # noqa: E402
from forward.core.costs import CommissionModel, SlippageModel  # noqa: E402
from forward.core.feeds import ListFeed  # noqa: E402
from forward.data.base import pivot_to_snapshots  # noqa: E402
from forward.data.history import load_cached  # noqa: E402
from forward.research.validate import (  # noqa: E402
    ValidationConfig,
    WalkForwardConfig,
    walk_forward,
)
from forward.signals.strategy import SignalEntryStrategy  # noqa: E402

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "backtest_production_rules.json"


def build_feed(config: dict):
    """One long panel of every symbol, then pivot to snapshots.

    `pivot_to_snapshots` needs a `symbol` column and an explicit timeframe, and
    `ListFeed` needs an instruments map -- `load_cached` frames carry neither.
    Every symbol must appear on every bar: `BacktestEngine._build_frames`
    NaN-pads any symbol missing at a step, so a sparse grid leaves
    `ctx.history(sym)` with no prices and the rules never fire -- which looks
    like "no edge".
    """
    import pandas as pd

    from forward.core.enums import Timeframe
    from forward.core.instrument_models import Instrument

    universe = config["universe"]
    exchange = config["exchange"]
    frames = load_cached(exchange, universe)
    if not frames:
        raise SystemExit("no cached history -- run scripts/fetch_long_history.py")

    # `load_cached` silently omits symbols it has no long-history file for.
    # Report that loudly: a quietly smaller universe changes the benchmark,
    # and a benchmark you did not notice changing is not a benchmark.
    dropped = [s for s in universe if s not in frames]
    if dropped:
        print(f"NOTE: {len(dropped)} symbol(s) absent from the long-history "
              f"cache and therefore excluded: {', '.join(dropped)}")
        print("      (fetch them with scripts/fetch_long_history.py to "
              "include them)\n")

    parts = []
    for symbol, frame in frames.items():
        f = frame.copy()
        f["symbol"] = symbol
        parts.append(f)
    combined = pd.concat(parts, ignore_index=True)

    snapshots = pivot_to_snapshots(combined, Timeframe.DAY_1)
    instruments = {
        s: Instrument(symbol=s, exchange=exchange) for s in frames
    }
    return ListFeed(snapshots, instruments), len(frames)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    config_path = parser.parse_args().config
    config = json.loads(config_path.read_text(encoding="utf-8"))
    print(f"Configuration: {config_path.resolve()}")
    universe = config["universe"]
    feed, n_sym = build_feed(config)
    snaps = feed.load()
    print(f"universe: {n_sym}/{len(universe)} symbols, {len(snaps)} sessions")
    first = snaps[0].ts
    last = snaps[-1].ts
    print(f"window  : {first:%Y-%m-%d} -> {last:%Y-%m-%d}\n")

    hdr = (f"{'train/test':>10} {'folds':>5} {'OOS ret':>9} {'OOS Shrp':>9} "
           f"{'B&H ret':>9} {'B&H Shrp':>9} {'MaxDD':>7} {'trades':>7} "
           f"{'P(real)':>8}  verdict")
    print(hdr)
    print("-" * len(hdr))

    rows = []
    bt = BacktestConfig(
        **config["backtest"],
        commission=CommissionModel(**config["commission"]),
        slippage=SlippageModel(**config["slippage"]),
    )

    for train, test in config["splits"]:
        result = walk_forward(
            feed,
            SignalEntryStrategy,
            {},
            config=WalkForwardConfig(
                train_bars=train, test_bars=test, **config["walk_forward"]
            ),
            validation=ValidationConfig(**config["validation"]),
            backtest=bt,
        )
        oos = result.oos_metrics
        bench = result.benchmark_metrics
        passed = result.verdict.passed
        rows.append((train, test, oos, bench, result))

        print(
            f"{train:>4}/{test:<5} {len(result.folds):>5} "
            f"{oos.total_return_pct:>8.2f}% {oos.sharpe:>9.2f} "
            f"{bench.total_return_pct:>8.2f}% {bench.sharpe:>9.2f} "
            f"{oos.max_drawdown_pct:>6.2f}% {oos.num_trades:>7} "
            f"{result.deflated_sharpe:>8.3f}  "
            f"{'PASS' if passed else 'FAIL'}"
        )

    # ---- the two questions that actually decide it ----------------------
    print("\n" + "=" * 66)
    sharpes = [r[2].sharpe for r in rows]
    beats = sum(1 for r in rows if r[2].sharpe > r[3].sharpe)
    passes = sum(1 for r in rows if r[4].verdict.passed)

    print(f"OOS Sharpe across splits : {min(sharpes):.2f} .. {max(sharpes):.2f} "
          f"(spread {max(sharpes) - min(sharpes):.2f})")
    print(f"Beat buy & hold          : {beats}/{len(rows)} splits")
    print(f"Passed the harness       : {passes}/{len(rows)} splits")

    verdict = "DO NOT TRADE" if passes == 0 else "review further"
    print(f"\nCONCLUSION: {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
