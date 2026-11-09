"""Morning briefing: pre-market Telegram with the day's list.

Pipeline (all local, no session needed at send time):
  evening  `forward history sync`   refresh the daily cache after close
  morning  `forward brief send`     score the cache, Telegram the picks (8:45 AM)

Levels are trigger-based, not predictions: longs trigger above the previous
day's high with the previous low as the stop. If price never triggers, there
is no trade — that discipline is the whole point.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

import pandas as pd

from forward.core.briefing_models import BriefingConfig
from forward.scanner import score_frame


def _rows(
    cfg: BriefingConfig, frames: Mapping[str, pd.DataFrame]
) -> pd.DataFrame:
    """Score supplied market frames using the briefing rules."""
    if cfg.universe == "watchlist":
        frames = {s: f for s, f in frames.items() if s in set(cfg.watchlist)}
    scored = []
    for symbol, df in frames.items():
        try:
            scored.append(score_frame(symbol, df))
        except Exception:  # noqa: BLE001
            continue
    scan = pd.DataFrame(scored)
    if scan.empty:
        return scan
    scan = scan[(scan["bars"] >= cfg.min_bars)
                & (scan["last"] >= cfg.min_price)
                & (scan["day_value_lakh"] >= cfg.min_day_value_lakh)
                & (scan["atr_pct"] >= cfg.min_atr_pct)]
    col = "vs_high" if cfg.ranking == "vs_high" else "score"
    return scan.sort_values(col, ascending=False).reset_index(drop=True)


def build_brief(
    cfg: BriefingConfig,
    frames: Mapping[str, pd.DataFrame],
    as_of: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Build a briefing message and its summary from market frames."""
    scan = _rows(cfg, frames)
    if scan.empty:
        return ("Forward morning brief: cache empty — run `forward history sync` after close.", {})

    bees = scan[scan["symbol"] == "NIFTYBEES-EQ"]
    regime = ("NIFTYBEES " + bees.iloc[0]["trend"]
              + f", RSI {bees.iloc[0]['rsi']:.0f}") if not bees.empty else "regime n/a"
    up = int((scan["trend"] == "UP").sum())
    breadth = f"{up}/{len(scan)} UP ({up / len(scan) * 100:.0f}%)"

    longs = scan[(scan["rsi"] < 80)
                 & ((scan["trend"] == "UP") | (scan["breakout"]) | (scan["rsi"] < 32))].head(cfg.top_n)
    if len(longs) < max(3, cfg.top_n // 2):
        longs = scan.head(cfg.top_n)
    avoids = scan.tail(cfg.avoid_n).iloc[::-1]

    day = as_of or date.today().strftime("%a %d %b")
    lines = [f"Forward morning brief — {day}", f"Regime: {regime} · breadth {breadth}", ""]
    lines.append("WATCH LONGS (trigger prev-high, exit at close):")
    for _, r in longs.iterrows():
        tag = "BO" if r["breakout"] else ("OS" if r["rsi"] < 32 else "MO")
        lines.append(f"{r['symbol'].replace('-EQ', '')} {r['last']} "
                     f"| trig {r['day_high']} | ref {r['day_low']} "
                     f"| RSI {r['rsi']:.0f} | {tag}")
    lines += ["", "AVOID / weak:"]
    for _, r in avoids.iterrows():
        lines.append(f"{r['symbol'].replace('-EQ', '')} {r['last']} "
                     f"| 1M {r['ret_1m']:+.1f}% | RSI {r['rsi']:.0f}")
    lines += ["", "No trigger = no trade. Exit at close — tested better than stops/targets. Not advice."]
    stats = {"scored": len(scan), "breadth_up": up,
             "longs": len(longs), "universe": cfg.universe}
    return "\n".join(lines), stats
