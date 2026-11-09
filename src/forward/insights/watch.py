"""What is worth knowing about each holding. Descriptions and risk control, never forecasts."""

from __future__ import annotations

from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict

from forward.insights.evidence import LAGGING_NOTE, PROTECT_NOTE, QUIET_NOTE
from forward.insights.stall import StallConfig, stall_frame


class WatchConfig(BaseModel):
    """Required thresholds for holding descriptions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    range_quiet_ratio: float
    protect_at_gain_pct: float
    trail_below_high_pct: float
    lagging_rs_pct: float


def watch_holding(
    holding: dict[str, Any],
    df: pd.DataFrame | None,
    stall_config: StallConfig,
    watch_config: WatchConfig,
    *,
    rs_20d: float | None = None,
) -> dict[str, Any]:
    """Build a holding summary with configured risk descriptions.

    Args:
        holding: Broker holding data for one symbol.
        df: Daily market bars for that symbol, if available.
        stall_config: Required periods and thresholds for stall features.
        watch_config: Required holding alert thresholds.
        rs_20d: Relative strength against Nifty over 20 days, when available.

    Returns:
        A holding summary with price, gain, and configured flags.
    """
    symbol = holding["symbol"]
    avg = float(holding.get("avg_price") or 0)
    row: dict[str, Any] = {
        "symbol": symbol.replace("-EQ", ""),
        "qty": holding.get("qty"),
        "avg_price": avg or None,
        "last": None,
        "pnl_pct": None,
        "as_of": None,
        "flags": [],
        "assessment_available": False,
    }
    if df is None or df.empty:
        row["assessment_error"] = "Historical bars are unavailable."
        return row

    df = df.sort_values("ts").reset_index(drop=True)
    last = float(df["close"].iloc[-1])
    row["last"] = round(last, 2)
    row["as_of"] = pd.Timestamp(df["ts"].iloc[-1]).strftime("%Y-%m-%d")
    pnl = ((last / avg) - 1) * 100 if avg > 0 else None
    row["pnl_pct"] = round(pnl, 1) if pnl is not None else None

    required_history = max(
        stall_config.sma_period,
        stall_config.return_period,
        stall_config.short_return_period,
        stall_config.high_period,
        stall_config.range_baseline_period,
        stall_config.rsi_period + stall_config.rsi_shift_period,
        stall_config.volume_baseline_period,
        stall_config.upper_wick_period,
    )
    if len(df) < required_history:
        row["assessment_error"] = (
            f"Only {len(df)} bars are available; {required_history} are required."
        )
        return row

    high20 = float(df["high"].tail(stall_config.high_period).max())

    # A big gain: name a level that keeps most of it, and say when price has slipped under it.
    if pnl is not None and pnl >= watch_config.protect_at_gain_pct:
        level = round(high20 * (1 - watch_config.trail_below_high_pct / 100), 2)
        kept = ((level / avg) - 1) * 100
        if last < level:
            row["flags"].append(
                {
                    "kind": "slipped",
                    "title": "Slipped below its protective level",
                    "detail": f"Up {pnl:.0f}% overall, but now under {level:,.2f} ({watch_config.trail_below_high_pct:.0f}% below its high).",
                    "level": level,
                    "note": PROTECT_NOTE,
                }
            )
        else:
            row["flags"].append(
                {
                    "kind": "protect",
                    "title": "Big gain to protect",
                    "detail": f"Up {pnl:.0f}%. A stop at {level:,.2f} would keep about {kept:.0f}% of it.",
                    "level": level,
                    "note": PROTECT_NOTE,
                }
            )

    f = stall_frame(df, stall_config).iloc[-1]
    quiet = (
        abs(f["ret5"]) <= stall_config.flat_return_pct
        and f["range_ratio"] <= watch_config.range_quiet_ratio
        and f["off_high"] >= -stall_config.near_high_distance_pct
    )
    if bool(quiet):
        row["flags"].append(
            {
                "kind": "quiet",
                "title": "Gone quiet near its high",
                "detail": (
                    f"Barely moved for {stall_config.short_return_period} days "
                    f"({abs(f['ret5']):.1f}%), within {abs(f['off_high']):.1f}% "
                    f"of its {stall_config.high_period}-day high."
                ),
                "level": None,
                "note": QUIET_NOTE,
            }
        )

    if rs_20d is not None and rs_20d <= watch_config.lagging_rs_pct and (pnl is None or pnl < 0):
        row["flags"].append(
            {
                "kind": "lagging",
                "title": "Lagging the market",
                "detail": f"{abs(rs_20d):.0f}% behind the Nifty over {stall_config.return_period} days.",
                "level": None,
                "note": LAGGING_NOTE,
            }
        )
    row["assessment_available"] = True
    return row
