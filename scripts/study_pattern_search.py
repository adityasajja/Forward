"""Can any combination of chart patterns reach a 60% chance of a +2% week?

Search every single pattern, pair and triple (plus a "volatile stock" condition) on
the EARLY half of the history only, keep whichever combinations scored highest,
then grade those same rules on the LATE half, which the search never saw.

The result to watch is the gap between the two. A rule that scores 55% in the half
it was chosen from and 33% in the half it was not is a description of the past, not a
signal. With hundreds of combinations tried, some will always look excellent by
chance; the late half is what tells the two apart.

Run:  ./.venv/Scripts/python.exe scripts/study_pattern_search.py
"""

from __future__ import annotations

import argparse
import json
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd
from study_chart_patterns import build

from forward.data.cached_market_frames import CachedMarketFramesAdapter
from forward.research.hunt import Costs

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "study_pattern_search.json"


def main() -> int:
    """Rank pattern combinations on the early half and report late results."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    config_path = parser.parse_args().config
    config = json.loads(config_path.read_text(encoding="utf-8"))
    pattern_config_path = ROOT / config["pattern_config"]
    pattern_config = json.loads(pattern_config_path.read_text(encoding="utf-8"))
    print(f"Configuration: {config_path.resolve()}")
    print(f"Pattern configuration: {pattern_config_path.resolve()}")
    stacked, columns, mid = build(CachedMarketFramesAdapter(), pattern_config)
    hit_target = pattern_config["hit_return_fraction"]
    round_trip = Costs(**pattern_config["costs"]).round_trip_pct() / 100
    output_path = ROOT / config["output_file"]
    # "Jumpy stock" is itself a condition worth combining: it is the strongest single
    # predictor of a big week, so the search should be free to use it.
    columns = dict(columns)
    columns["volatile_top_quintile"] = pd.Series(
        stacked["vol_bucket"].to_numpy() == stacked["vol_bucket"].max(), index=stacked.index
    )

    early = (stacked["date"] < mid).to_numpy()
    late = ~early
    hit = stacked["hit"].to_numpy()
    fwd = stacked["fwd"].to_numpy()
    names = list(columns)
    matrix = np.column_stack([columns[n].to_numpy() for n in names])

    base_early, base_late = float(hit[early].mean()), float(hit[late].mean())
    print(
        f"stock-weeks {len(stacked):,}   base rate: early {100 * base_early:.1f}%  late {100 * base_late:.1f}%"
    )

    tried = []
    for size in config["combination_sizes"]:
        for combo in combinations(range(len(names)), size):
            mask = matrix[:, combo].all(axis=1)
            train = mask & early
            n = int(train.sum())
            if n < config["minimum_train_occurrences"]:
                continue
            tried.append((float(hit[train].mean()), n, combo, mask))

    tried.sort(key=lambda t: -t[0])
    print(
        f"{len(tried):,} combinations had at least "
        f"{config['minimum_train_occurrences']} early-half occurrences\n"
    )
    print(
        f"{'rule (all must be true)':<70}{'early P':>8}{'n':>7}{'| late P':>9}{'n':>7}{'net%/wk':>9}"
    )

    rows = []
    for p_train, n_train, combo, mask in tried[: config["top_rules"]]:
        test = mask & late
        n_test = int(test.sum())
        p_test = float(hit[test].mean()) if n_test else float("nan")
        net = float(fwd[test].mean() - round_trip) * 100 if n_test else float("nan")
        label = " AND ".join(names[i] for i in combo)
        rows.append(
            {
                "rule": label,
                "early_p_hit": round(100 * p_train, 2),
                "early_n": n_train,
                "late_p_hit": round(100 * p_test, 2),
                "late_n": n_test,
                "late_net_pct_per_week": round(net, 3),
            }
        )
        print(
            f"{label[:69]:<70}{100 * p_train:>8.1f}{n_train:>7}{'| ' + format(100 * p_test, '.1f'):>9}{n_test:>7}{net:>9.3f}"
        )

    # How much of the early-half score is luck? Shuffle the outcomes and rerun the
    # identical search: the best combination it finds is what pure noise achieves.
    rng = np.random.default_rng(config["shuffle_seed"])
    noise_best = []
    for _ in range(config["shuffle_iterations"]):
        shuffled = rng.permutation(hit[early])
        best = 0.0
        for _, _n_train, _combo, mask in tried:
            sel = mask[early]
            best = max(best, float(shuffled[sel].mean()))
        noise_best.append(best)
    print(
        f"\nbest early-half score the SAME search finds on shuffled (meaningless) outcomes: "
        f"{100 * np.mean(noise_best):.1f}%   <- what luck alone achieves"
    )

    minimum_late = config["minimum_late_sample_for_summary"]
    target_probability = config["target_hit_probability_pct"]
    top_late = [r["late_p_hit"] for r in rows if r["late_n"] >= minimum_late]
    best_late = max(
        (r for r in rows if r["late_n"] >= minimum_late),
        key=lambda r: r["late_p_hit"],
        default=None,
    )
    over60 = [
        r for r in rows if r["late_n"] >= minimum_late and r["late_p_hit"] >= target_probability
    ]
    print(
        f"\nrules chosen on the early half that reach {target_probability}% on the late half: {len(over60)} of {len(rows)}"
    )
    if best_late:
        print(
            f"best late-half result among the {len(rows)} chosen rules: {best_late['late_p_hit']}%  ({best_late['rule']})"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            {
                "target": f"P(next week >= {int(hit_target * 100)}%) of {target_probability}% or more",
                "base_rate_early_pct": round(100 * base_early, 2),
                "base_rate_late_pct": round(100 * base_late, 2),
                "combinations_searched": len(tried),
                "best_early_score_on_shuffled_outcomes_pct": round(
                    100 * float(np.mean(noise_best)), 2
                ),
                "chosen_on_early_half_graded_on_late_half": rows,
                "reaching_60_on_late_half": len(over60),
                "median_late_p_hit_of_chosen": round(float(np.median(top_late)), 2)
                if top_late
                else None,
                "survivorship_note": "universe is today's long-history names; failed companies are absent",
            },
            indent=1,
        ),
        encoding="utf8",
    )
    print(f"Results: {output_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
