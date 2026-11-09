"""Does the momentum edge survive its own search?

`research_weekly_momentum.py` reported the best of 90 configurations at Sharpe
1.83 with z=+9.9 against a matched control. Both numbers are real, and both are
also exactly what a search produces: the best of 90 draws is high by
construction, and the z is measured against the control's standard error rather
than against the cost of having searched.

Two corrections are applied here, and they are independent.

1. **Deflated Sharpe** (Bailey & Lopez de Prado) — the hurdle a Sharpe must
   clear once you admit how many combinations were tried. Every trial's Sharpe
   goes in, including the rejected ones, plus the skew and kurtosis of the
   winner's own return series, because weekly equity returns are not normal.

2. **Walk-forward selection** — the configs are fixed rules with no fitted
   parameters, so the only overfitting channel is *which config you pick*. That
   is testable directly: choose the best config on the training window, then
   score it on the window that follows. If the ranking does not carry over, the
   1.83 is a fact about 2015-2026 in aggregate and not a rule.

Usage::

    .venv/Scripts/python.exe scripts/validate_momentum_deflated.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from research_weekly_momentum import (  # noqa: E402
    build_matrix,
    simulate,
    universe_symbols,
    weekly_metrics,
)

from forward.research.validate import deflated_sharpe_ratio  # noqa: E402

CONFIG_PATH = ROOT / "config" / "validate_momentum_deflated.json"


def all_configs(config: dict):
    """Yield each pre-registered configuration from the weekly study."""
    grid = config["momentum_grid"]
    for lookback in grid["lookback_weeks"]:
        for skip in grid["skip_weeks"]:
            for top_n in grid["top_n"]:
                yield lookback, skip, top_n


def sharpe_of(r: np.ndarray, config: dict) -> float | None:
    """Return Sharpe from the named weekly-study configuration."""
    m = weekly_metrics(r, config)
    return m.get("sharpe") if m else None


def main() -> int:
    """Run the configured deflated-Sharpe and walk-forward study."""
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    args = parser.parse_args()
    settings = json.loads(args.config.read_text(encoding="utf-8"))
    print(f"Configuration: {args.config.resolve()}")
    weekly_config_path = ROOT / settings["weekly_momentum_config"]
    weekly_config = json.loads(weekly_config_path.read_text(encoding="utf-8"))
    start = pd.Timestamp(weekly_config["history_start_date"])
    end = pd.Timestamp(weekly_config["history_end_date"])
    payload: dict = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "purpose": "multiple-testing and out-of-sample check on the weekly "
                   "momentum result",
        "universes": {},
    }
    pooled_sharpes: list[float] = []

    for name in weekly_config["universe_names"]:
        syms = universe_symbols(name, weekly_config)
        W, R = build_matrix(syms, start, end, weekly_config)
        print(f"\n══ {name}: {W.shape[1]} names, {W.shape[0]} weeks")

        # ---- 1. every trial, then the deflated hurdle --------------------
        trial_sharpes: list[float] = []
        best = {"sharpe": -9e9}
        for lookback, skip, top_n in all_configs(weekly_config):
            r = simulate(
                W, R, lookback, skip, top_n, None,
                weekly_config["execution_lag_weeks"], weekly_config,
            )
            if r is None:
                continue
            m = weekly_metrics(r, weekly_config)
            if not m or m.get("sharpe") is None:
                continue
            trial_sharpes.append(m["sharpe"])
            if m["sharpe"] > best["sharpe"]:
                best = {"sharpe": m["sharpe"], "lookback": lookback,
                        "skip": skip, "top_n": top_n, "metrics": m, "returns": r}
        if not trial_sharpes or "returns" not in best:
            raise ValueError(f"No valid momentum configuration for universe {name!r}.")

        r = best["returns"]
        r = r[np.isfinite(r)]
        n_obs = int(len(r))
        skew = float(pd.Series(r).skew())
        kurt = float(pd.Series(r).kurtosis() + 3.0)
        dsf, hurdle = deflated_sharpe_ratio(trial_sharpes, n_obs, skew, kurt)

        print(f"   best config: L={best['lookback']}w skip={best['skip']} "
              f"N={best['top_n']}  Sharpe {best['sharpe']:.3f}")
        print(f"   {len(trial_sharpes)} trials · skew {skew:+.2f} · kurtosis {kurt:.2f}")
        print(f"   multiple-testing hurdle Sharpe {hurdle:.3f}  →  "
              f"P(edge) = {dsf:.3f}  {'PASS' if dsf >= 0.95 else 'FAIL'}")

        # ---- 2. walk-forward: pick on train, score on test ---------------
        weeks = W.index
        n_w = len(weeks)
        fold_count = settings["fold_count"]
        fold_len = n_w // (fold_count + 1)
        folds = []
        for k in range(fold_count):
            train_end = fold_len * (k + 1)
            test_end = min(train_end + fold_len, n_w)
            if (
                test_end - train_end < settings["minimum_test_weeks"]
                or train_end < settings["minimum_training_weeks"]
            ):
                continue
            Wtr, Rtr = W.iloc[:train_end], R.iloc[:train_end]
            Wte, Rte = W.iloc[:test_end], R.iloc[:test_end]
            # Choose on train only.
            chosen, chosen_sharpe = None, -9e9
            for lookback, skip, top_n in all_configs(weekly_config):
                rt = simulate(
                    Wtr, Rtr, lookback, skip, top_n, None,
                    weekly_config["execution_lag_weeks"], weekly_config,
                )
                if rt is None:
                    continue
                s = sharpe_of(rt, weekly_config)
                if s is not None and s > chosen_sharpe:
                    chosen, chosen_sharpe = (lookback, skip, top_n), s
            if chosen is None:
                continue
            # Score the chosen rule on the test window, measured on the test
            # tail only.
            full = simulate(
                Wte, Rte, chosen[0], chosen[1], chosen[2], None,
                weekly_config["execution_lag_weeks"], weekly_config,
            )
            if full is None:
                continue
            tail = full[train_end:]
            tail = tail[np.isfinite(tail)]
            if len(tail) < settings["minimum_test_weeks"]:
                continue
            te = weekly_metrics(tail, weekly_config)
            # And what the fold-best config would have scored, for contrast.
            best_te, best_te_sharpe = None, -9e9
            for lookback, skip, top_n in all_configs(weekly_config):
                ft = simulate(
                    Wte, Rte, lookback, skip, top_n, None,
                    weekly_config["execution_lag_weeks"], weekly_config,
                )
                if ft is None:
                    continue
                tt = ft[train_end:]
                tt = tt[np.isfinite(tt)]
                if len(tt) < settings["minimum_test_weeks"]:
                    continue
                s = sharpe_of(tt, weekly_config)
                if s is not None and s > best_te_sharpe:
                    best_te, best_te_sharpe = (lookback, skip, top_n), s
            folds.append({
                "fold": k + 1,
                "train_weeks": train_end,
                "test_weeks": len(tail),
                # Label the fold's own block. Using weeks[-1] here would print
                # the same end date for every fold and make five disjoint test
                # blocks look like five overlapping ones.
                "test_window": [str(weeks[train_end].date()),
                                str(weeks[test_end - 1].date())],
                "chosen_on_train": {"lookback": chosen[0], "skip": chosen[1],
                                    "top_n": chosen[2], "train_sharpe": round(chosen_sharpe, 3)},
                "oos_sharpe": te.get("sharpe") if te else None,
                "oos_mean_weekly_pct": te.get("mean_weekly_pct") if te else None,
                "hindsight_best": {"lookback": best_te[0], "skip": best_te[1],
                                   "top_n": best_te[2]} if best_te else None,
                "hindsight_best_oos_sharpe": round(best_te_sharpe, 3) if best_te else None,
            })
            if te:
                print(f"   fold {k+1}: chose L={chosen[0]}w skip={chosen[1]} "
                      f"N={chosen[2]} (train Sharpe {chosen_sharpe:+.2f}) → "
                      f"OOS Sharpe {te.get('sharpe'):+.3f}  "
                      f"mean {te.get('mean_weekly_pct'):+.3f}%/wk   "
                      f"[hindsight best OOS {best_te_sharpe:+.2f}]")

        if not folds:
            raise ValueError(f"No walk-forward fold meets the config for {name!r}.")

        oos = [f["oos_sharpe"] for f in folds if f["oos_sharpe"] is not None]
        oos_mean = [f["oos_mean_weekly_pct"] for f in folds
                    if f["oos_mean_weekly_pct"] is not None]
        hind = [f["hindsight_best_oos_sharpe"] for f in folds
                if f["hindsight_best_oos_sharpe"] is not None]
        summary = {
            "best_config": {k: best[k] for k in ("lookback", "skip", "top_n")},
            "best_sharpe_in_sample": round(best["sharpe"], 3),
            "trials": len(trial_sharpes),
            "skew": round(skew, 3),
            "kurtosis": round(kurt, 3),
            "deflated_sharpe": round(dsf, 4),
            "required_sharpe": round(hurdle, 3),
            "passes_deflated": bool(dsf >= 0.95),
            "walk_forward": folds,
            "oos_sharpe_mean": round(float(np.mean(oos)), 3) if oos else None,
            "oos_sharpe_min": round(float(np.min(oos)), 3) if oos else None,
            "oos_sharpe_max": round(float(np.max(oos)), 3) if oos else None,
            "oos_mean_weekly_pct_mean": round(float(np.mean(oos_mean)), 4) if oos_mean else None,
            "hindsight_best_oos_mean": round(float(np.mean(hind)), 3) if hind else None,
            "selection_loss_sharpe": round(float(np.mean(hind) - np.mean(oos)), 3)
            if oos and hind else None,
        }
        payload["universes"][name] = summary
        pooled_sharpes.extend(trial_sharpes)

        print(f"   OOS Sharpe across folds: mean "
              f"{summary['oos_sharpe_mean']}, range "
              f"{summary['oos_sharpe_min']} to {summary['oos_sharpe_max']}")
        print(f"   hindsight-best OOS mean {summary['hindsight_best_oos_mean']} "
              f"vs chosen {summary['oos_sharpe_mean']} → selection cost "
              f"{summary['selection_loss_sharpe']}")

    # ---- 3. pooled across all universes --------------------------------
    if pooled_sharpes:
        vals = np.asarray(pooled_sharpes, dtype=float)
        hurdle = None
        from forward.research.validate import expected_max_sharpe
        hurdle = expected_max_sharpe(vals, len(vals))
        payload["pooled"] = {
            "trials": int(len(vals)),
            "best_sharpe": round(float(vals.max()), 3),
            "required_sharpe": round(hurdle, 3),
            "note": "the pooled hurdle is the bar the single best trial must "
                    "clear once all universes are counted as one search",
        }
        print(f"\n══ pooled: {len(vals)} trials, best {vals.max():.3f}, "
              f"hurdle {hurdle:.3f}")

    out = ROOT / settings["output_file"]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf8")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
