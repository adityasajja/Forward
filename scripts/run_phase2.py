"""Run and log the phase-two walk-forward research job."""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "run_phase2.json"
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import strategy_hunt as sh  # noqa: E402


class Tee:
    """Write output to the terminal and the configured log file."""

    def __init__(self, *streams) -> None:
        self._streams = streams

    def write(self, value: str) -> int:
        """Write a string to each stream.

        Args:
            value: Output text to write.

        Returns:
            Number of characters written.
        """
        for stream in self._streams:
            stream.write(value)
            stream.flush()
        return len(value)

    def flush(self) -> None:
        """Flush every destination stream."""
        for stream in self._streams:
            stream.flush()


def main() -> int:
    """Run the configured study and save the results."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    config_path = parser.parse_args().config
    config = json.loads(config_path.read_text(encoding="utf-8"))
    print(f"Configuration: {config_path.resolve()}")
    log_path = ROOT / config["log_file"]
    output_path = ROOT / config["output_file"]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    start = pd.Timestamp(config["history_start_date"])
    end = pd.Timestamp(config["history_end_date"])
    symbols = list(
        dict.fromkeys(
            symbol
            for relative_path in config["universe_files"]
            for symbol in sh.parse_universe(ROOT / relative_path)
        )
    )

    with log_path.open("w", encoding="utf-8", buffering=1) as log_stream:
        tee = Tee(sys.stdout, log_stream)
        with redirect_stdout(tee), redirect_stderr(Tee(sys.stderr, log_stream)):
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Universe: {len(symbols)} symbols")
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Window: {start.date()} → {end.date()}")
            result = sh.phase2_walkforward(symbols, start, end)
            output_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Results saved to {output_path}")
            if result.get("folds"):
                print(f"\n{'=' * 72}")
                print("PHASE 2 SUMMARY:")
                print(f"  Avg test Sharpe: {result['avg_test_sharpe']:.3f}")
                print(f"  Avg test CAGR: {result['avg_test_cagr_pct']:.2f}%")
                print(f"  Avg weekly mean: {result['avg_test_weekly_mean_pct']:+.4f}%")
                print(f"  Avg excess vs control: {result['avg_test_excess_vs_control']:+.3f}")
                print(f"  Deflated Sharpe: {result.get('deflated_sharpe')}")
                print(f"  Verdict: {result['verdict']}")
                for fold in result["folds"]:
                    print(
                        f"\n  Fold: {fold['signal']}/k{fold['k']}/step{fold['step']}/{fold['sizing']}"
                    )
                    print(
                        f"    Train Sharpe={fold['train_sharpe']:.3f} → Test Sharpe={fold['test_sharpe']:.3f}"
                    )
                    print(
                        f"    Train weekly={fold['train_weekly_mean']:+.4f}% → Test weekly={fold['test_weekly_mean_pct']:+.4f}%"
                    )
                    print(
                        f"    Test CAGR={fold['test_cagr_pct']:.2f}% DD={fold['test_max_dd_pct']:.1f}%"
                    )
                    print(
                        f"    Test P(>=5%)={fold['test_weekly_p_ge_5pct']}% ex_ctrl={fold['test_excess_sharpe']:+.3f}"
                    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
