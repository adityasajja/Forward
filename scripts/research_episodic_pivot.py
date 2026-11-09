"""Does the Episodic Pivot's *premise* exist on the tested universe?

Why this runs before any backtest
---------------------------------
The playbook is explicit about the raw material it needs: a stock that has been
"ignored for a long time", a catalyst that arrives as "gaps of 20–40% or more",
and repricing of "50–300% or more in a short period", "often in as little as
10–20 trading days". Its worked examples are SMCI (+189% in 28 sessions), ROOT
(+358% in 16) and ANF (+240%) — all small, genuinely neglected US companies.

Those are measurable claims about a *pond*. If the pond does not contain the
fish, no amount of careful entry logic will produce the trade, and a backtest
returning zero trades would be misread as "the rules are wrong" rather than
"the universe cannot express the setup". So this script measures the premise
first, and measures it on more than one universe, because the interesting
comparison is Nifty 50 against the mid-cap pond the playbook actually fishes in.

What is measured, per universe
------------------------------
* **Gap frequency** — how often a session opens >= 3 / 5 / 10 / 20% above the
  prior close.
* **Volume-spike frequency** — how often volume is >= 3 / 5 / 10 × its own
  trailing 20-session average.
* **Joint catalyst frequency** — gap >= 5% *and* volume >= 3×, the two-pillar
  trigger every EP rule in ``episodic_pivot.py`` is built on.
* **Quiet-tape frequency** — share of sessions where 120-session realised
  volatility is under 2.5%/day, the playbook's "neglect".
* **The payoff, measured not quoted** — forward 10- and 20-session return after
  a joint catalyst, versus the unconditional distribution over the same bars.
  This is the EP bet: does the catalyst day actually predict anything?
* **Tail availability** — the best 20-session return anywhere in the history,
  and how many 20-session windows exceeded +50% / +100%. The playbook's headline
  numbers live out here.

Usage::

    .venv/Scripts/python.exe scripts/research_episodic_pivot.py
    .venv/Scripts/python.exe scripts/research_episodic_pivot.py --config config/research_episodic_pivot.json
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "research_episodic_pivot.json"


def universe_symbols(name: str, config: dict) -> list[str]:
    """Read symbols from the configured universe file."""
    try:
        path = ROOT / config["universe_files"][name]
    except KeyError as exc:
        raise ValueError(f"Universe {name!r} is not configured.") from exc
    with path.open(encoding="utf-8-sig") as handle:
        symbols = [r[config["symbol_column"]].strip().upper()
                   for r in csv.DictReader(handle) if r.get(config["symbol_column"])]
    if not symbols:
        raise ValueError(f"Configured universe file has no symbols: {path}.")
    return symbols


def load(symbols: list[str], config: dict) -> dict[str, pd.DataFrame]:
    """Load every requested symbol and apply the configured history gate."""
    from forward.research.data_hygiene import drop_reverting_spikes

    out: dict[str, pd.DataFrame] = {}
    dropped_total = 0
    for symbol in symbols:
        path = ROOT / config["daily_cache_directory"] / f"{symbol}.parquet"
        if not path.is_file():
            raise FileNotFoundError(f"Required history is missing for {symbol}: {path}.")
        frame = pd.read_parquet(path)
        frame.columns = [c.strip().lower() for c in frame.columns]
        required_columns = set(config["required_columns"])
        missing_columns = required_columns.difference(frame.columns)
        if missing_columns:
            raise ValueError(f"{path} is missing required columns: {sorted(missing_columns)}.")
        if len(frame) < config["minimum_history_bars"]:
            continue
        frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
        frame = (
            frame.dropna(subset=["open", "high", "low", "close"])
            .sort_values("ts")
            .drop_duplicates("ts", keep="last")
            .reset_index(drop=True)
        )
        # A 3x bar on a quiet tape *is* an Episodic Pivot as far as any of these
        # statistics can tell. Left in, the vendor's 2021-09-15 glitch would be
        # reported as the best 20-session move in the index.
        frame, dropped = drop_reverting_spikes(frame)
        dropped_total += len(dropped)
        if len(frame) >= config["minimum_history_bars"]:
            out[symbol] = frame
    if not out:
        raise ValueError("No requested symbol meets the configured history gate.")
    if dropped_total:
        print(f"  dropped {dropped_total} corrupt bar(s) — see forward.research.data_hygiene")
    return out


def enrich(frame: pd.DataFrame, config: dict) -> pd.DataFrame:
    """The same columns ``episodic_pivot.py`` builds, computed independently.

    Deliberately duplicated rather than imported: this script is the check on
    that module, and a check that calls the thing it is checking proves nothing.
    """
    close = frame["close"].astype(float)
    volume = frame["volume"].astype(float).where(lambda v: v > 0)
    prev = close.shift(1)
    out = pd.DataFrame(index=frame.index)
    out["gap_pct"] = (frame["open"].astype(float) / prev - 1.0) * 100.0
    out["ret_1d"] = close.pct_change() * 100.0
    out["vol_ratio"] = volume / volume.rolling(
        config["volume_average_window"], min_periods=config["volume_average_minimum"]
    ).mean().shift(1)
    out["vol_120"] = close.pct_change().rolling(
        config["quiet_volatility_window"],
        min_periods=config["quiet_volatility_minimum"],
    ).std() * 100.0
    out["turnover_cr"] = volume * close / config["rupees_per_crore"]
    for horizon in config["forward_horizons"]:
        out[f"fwd_{horizon}"] = close.shift(-horizon) / close - 1.0
    window = config["tail_window"]
    out["tail_max"] = close.shift(-1).rolling(window).max().shift(-(window - 1)) / close - 1.0
    return out


def pct(part: int, whole: int) -> float:
    """Return a percentage for a non-empty sample."""
    if whole <= 0:
        raise ValueError("Cannot calculate a percentage without observations.")
    return 100.0 * part / whole


def optional_percent(value: float | None, decimals: int = 2) -> str:
    """Format an optional result, showing missing samples as unavailable."""
    if value is None or pd.isna(value):
        return "n/a"
    return f"{value:+.{decimals}f}%"


def describe(name: str, series: dict[str, pd.DataFrame], config: dict) -> dict:
    """Measure catalyst frequency and forward outcomes for one universe."""
    quiet_max = config["quiet_max_volatility_pct_per_day"]
    frames = {s: enrich(f, config) for s, f in series.items()}
    stack = pd.concat(frames.values(), keys=frames.keys(), names=["symbol", "i"])
    stack = stack.reset_index(level=0)
    valid = stack.dropna(subset=["gap_pct", "vol_120"])
    if valid.empty:
        raise ValueError(f"Universe {name!r} has no valid factor observations.")
    n = len(valid)

    gaps = {t: int((valid["gap_pct"] >= t).sum()) for t in config["gap_thresholds_pct"]}
    spikes = {t: int((valid["vol_ratio"] >= t).sum()) for t in config["volume_ratio_thresholds"]}
    catalyst = valid[
        (valid["gap_pct"] >= config["catalyst_gap_threshold_pct"])
        & (valid["vol_ratio"] >= config["catalyst_volume_ratio_threshold"])
    ]
    quiet = valid[valid["vol_120"] <= quiet_max]

    report_horizon = config["reported_forward_horizon"]
    tail_thresholds_pct = [int(round(value * 100)) for value in config["tail_return_thresholds"]]
    one_day_thresholds = config["one_day_move_thresholds_pct"]
    fwd_all = valid[f"fwd_{report_horizon}"].dropna()
    fwd_cat = catalyst[f"fwd_{report_horizon}"].dropna()
    fwd_quiet_cat = catalyst[catalyst["vol_120"] <= quiet_max][f"fwd_{report_horizon}"].dropna()

    roll_max = valid["tail_max"].dropna()
    # `valid` is a stack, so its row index repeats across symbols: `.loc[idxmax()]`
    # selects *every* symbol's row at that position and `.iloc[0]` then reports
    # whichever symbol happens to sit first — a wrong answer that looks like a
    # measurement. Positional argmax is the only unambiguous form here.
    if roll_max.empty:
        raise ValueError("Not enough history to evaluate the configured tail window.")
    pos = int(np.nanargmax(valid["tail_max"].to_numpy(dtype=float)))
    best_symbol = str(valid["symbol"].iloc[pos])
    best_move = round(float(valid["tail_max"].iloc[pos]) * 100, 1)

    report = {
        "universe": name,
        "symbols": len(series),
        "symbol_bars": n,
        "window": f"{min(f['ts'].min() for f in series.values()):%Y-%m-%d} → "
                  f"{max(f['ts'].max() for f in series.values()):%Y-%m-%d}",
        "gap_days": gaps,
        "gap_pct_of_days": {k: round(pct(v, n), 3) for k, v in gaps.items()},
        "volume_spike_days": spikes,
        "volume_spike_pct_of_days": {k: round(pct(v, n), 3) for k, v in spikes.items()},
        "joint_catalyst_days": int(len(catalyst)),
        "joint_catalyst_pct_of_days": round(pct(len(catalyst), n), 3),
        "quiet_tape_pct_of_days": round(pct(len(quiet), n), 2),
        "quiet_and_catalyst_days": int(len(catalyst[catalyst["vol_120"] <= quiet_max])),
        f"fwd{report_horizon}_unconditional_pct": round(float(fwd_all.mean()) * 100, 2) if len(fwd_all) else None,
        f"fwd{report_horizon}_after_catalyst_pct": round(float(fwd_cat.mean()) * 100, 2) if len(fwd_cat) else None,
        f"fwd{report_horizon}_after_quiet_catalyst_pct": (
            round(float(fwd_quiet_cat.mean()) * 100, 2) if len(fwd_quiet_cat) else None
        ),
        f"fwd{report_horizon}_after_catalyst_hit_rate": (
            round(float((fwd_cat > 0).mean()) * 100, 1) if len(fwd_cat) else None
        ),
        f"fwd{report_horizon}_after_catalyst_n": int(len(fwd_cat)),
        "best_tail_move_pct": best_move,
        "best_tail_symbol": best_symbol,
    }
    for threshold, threshold_pct in zip(config["tail_return_thresholds"], tail_thresholds_pct, strict=True):
        count = int((roll_max > threshold).sum())
        report[f"tail_windows_over_{threshold_pct}_count"] = count
        report[f"tail_windows_over_{threshold_pct}_share"] = round(pct(count, len(roll_max)), 4)
    for threshold in one_day_thresholds:
        report[f"one_day_moves_over_{threshold}_count"] = int(
            (valid["ret_1d"].abs() >= threshold).sum()
        )
    return report


def render(reports: list[dict], config: dict) -> str:
    lines: list[str] = []
    horizon = config["reported_forward_horizon"]
    tail_thresholds_pct = [int(round(value * 100)) for value in config["tail_return_thresholds"]]
    one_day_thresholds = config["one_day_move_thresholds_pct"]
    for r in reports:
        g, gp = r["gap_days"], r["gap_pct_of_days"]
        v, vp = r["volume_spike_days"], r["volume_spike_pct_of_days"]
        lines += [
            f"── {r['universe']} " + "─" * max(0, 62 - len(r["universe"])),
            f"  {r['symbols']} symbols, {r['symbol_bars']:,} symbol-bars, {r['window']}",
            "",
            "  gap up on the open, share of all sessions",
            *[f"    >= {threshold}% : {g[threshold]:>6,}  ({gp[threshold]:.2f}%)"
              for threshold in config["gap_thresholds_pct"]],
            "",
            f"  volume vs its own trailing {config['volume_average_window']}-session average",
            *[f"    >= {threshold}x : {v[threshold]:>6,}  ({vp[threshold]:.2f}%)"
              for threshold in config["volume_ratio_thresholds"]],
            "",
            f"  the EP trigger (gap >= {config['catalyst_gap_threshold_pct']}% AND volume >= "
            f"{config['catalyst_volume_ratio_threshold']}x)",
            f"    sessions            : {r['joint_catalyst_days']:,} "
            f"({r['joint_catalyst_pct_of_days']:.3f}% of all)",
            f"    of those, quiet tape: {r['quiet_and_catalyst_days']:,} "
            f"({config['quiet_volatility_window']}d vol <= "
            f"{config['quiet_max_volatility_pct_per_day']}%/day; quiet tape is "
            f"{r['quiet_tape_pct_of_days']:.1f}% of sessions)",
            "",
            f"  does the catalyst predict anything? (forward {config['reported_forward_horizon']}-session return)",
            f"    unconditional       : {optional_percent(r[f'fwd{horizon}_unconditional_pct'])}",
            f"    after a catalyst    : {optional_percent(r[f'fwd{horizon}_after_catalyst_pct'])} "
            f"over n={r[f'fwd{horizon}_after_catalyst_n']:,} "
            f"(up {optional_percent(r[f'fwd{horizon}_after_catalyst_hit_rate'], 1)} of the time)",
            f"    after quiet+catalyst: {optional_percent(r[f'fwd{horizon}_after_quiet_catalyst_pct'])}",
            "",
            f"  the playbook's headline payoffs (best {config['tail_window']}-session window, any symbol)",
            f"    best anywhere       : {r['best_tail_move_pct']:+.1f}% "
            f"({r['best_tail_symbol']})",
            *[f"    {config['tail_window']}d windows > +{threshold}% : "
              f"{r[f'tail_windows_over_{threshold}_count']:,} "
              f"({r[f'tail_windows_over_{threshold}_share']:.3f}% of windows)"
              for threshold in tail_thresholds_pct],
            "    one-day absolute moves: "
            + "   ".join(
                f">= {threshold}%: {r[f'one_day_moves_over_{threshold}_count']:,}"
                for threshold in one_day_thresholds
            ),
            "",
        ]
    return "\n".join(lines)


def main() -> int:
    """Run the configured episodic-pivot premise study."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", type=Path, default=CONFIG_PATH)
    config_path = ap.parse_args().config
    config = json.loads(config_path.read_text(encoding="utf-8"))
    print(f"Configuration: {config_path.resolve()}")

    reports: list[dict] = []
    universe_names = config["universes"]
    if not universe_names:
        raise ValueError("The configured universe list is empty.")
    for name in universe_names:
        series = load(universe_symbols(name, config), config)
        print(f"  {name}: {len(series)} symbols with >= {config['minimum_history_bars']} bars")
        reports.append(describe(name, series, config))

    print()
    print(render(reports, config))

    out = ROOT / config["output_file"]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {"generated_at": datetime.now().isoformat(timespec="seconds"),
             "config_path": str(config_path.resolve()),
             "quiet_max_vol": config["quiet_max_volatility_pct_per_day"],
             "universes": reports},
            indent=2,
        ),
        encoding="utf8",
    )
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
