"""Per-bar "stalled at the top" features. Research and building blocks, NOT an alert rule.

``scripts/study_stall.py`` and ``scripts/study_exit_signals.py`` tested whether "ran up, sits
at its high, has gone flat" precedes weak weeks, over 2,867 stocks and 11 years. It does not:
flagged stocks did slightly *better* than average over the next 5 sessions (+0.65% against
+0.37%), and none of nine topping signals held up in both halves of the history. Stocks at
their highs tend to keep drifting up. So nothing here is used to say "sell, it has peaked".

What survives is narrower: a stock that has gone quiet tends to stay quieter than usual
(``scripts/study_quiet.py``), which ``forward.insights.watch`` reports as a description, not a
forecast of direction. The features are kept because that check and the studies use them.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict

from forward.strategy.indicators import rsi


class StallConfig(BaseModel):
    """Required thresholds used by the stalled-stock description."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sma_period: int
    return_period: int
    short_return_period: int
    high_period: int
    range_short_period: int
    range_baseline_period: int
    rsi_period: int
    rsi_shift_period: int
    volume_short_period: int
    volume_baseline_period: int
    upper_wick_period: int
    run_return_pct: float
    extension_pct: float
    near_high_distance_pct: float
    flat_return_pct: float
    flat_range_ratio: float
    run_score_cap_pct: float
    run_score_points: float
    flat_return_score_points: float
    flat_range_score_points: float
    range_ratio_score_cap: float
    momentum_rsi_minimum: float
    momentum_rsi_drop: float
    momentum_drop_points: float
    momentum_level_points: float
    volume_ratio_maximum: float
    low_volume_points: float
    upper_wick_minimum: float
    upper_wick_points: float
    score_threshold: float
    score_maximum: float


def stall_frame(df: pd.DataFrame, config: StallConfig) -> pd.DataFrame:
    """Return per-bar features using required threshold configuration.

    Args:
        df: Historical bars with timestamps and OHLCV values.
        config: Required thresholds and indicator periods.

    Returns:
        A frame with detector features, score, and flag columns.
    """
    df = df.sort_values("ts").reset_index(drop=True)
    c = df["close"].astype(float)
    h = df["high"].astype(float)
    lo = df["low"].astype(float)
    o = df["open"].astype(float)
    if "volume" not in df.columns:
        raise ValueError("Stall features require volume data.")
    v = df["volume"].astype(float)

    sma50 = c.rolling(config.sma_period).mean()
    ext50 = (c / sma50 - 1) * 100
    ret20 = (c / c.shift(config.return_period) - 1) * 100
    ret5 = (c / c.shift(config.short_return_period) - 1) * 100
    off_high = (c / h.rolling(config.high_period).max() - 1) * 100

    span = h - lo
    range_ratio = span.rolling(config.range_short_period).mean() / span.rolling(
        config.range_baseline_period
    ).mean().replace(0, np.nan)
    rsi_now = rsi(c, config.rsi_period)
    rsi_before = rsi_now.shift(config.rsi_shift_period)
    vol_ratio = v.rolling(config.volume_short_period).mean() / v.rolling(
        config.volume_baseline_period
    ).mean().replace(0, np.nan)
    upper_wick = (
        ((h - np.maximum(o, c)) / span.replace(0, np.nan)).rolling(config.upper_wick_period).mean()
    )

    ran_up = (ret20 >= config.run_return_pct) | (ext50 >= config.extension_pct)
    at_top = off_high >= -config.near_high_distance_pct
    stalled = (ret5.abs() <= config.flat_return_pct) & (range_ratio <= config.flat_range_ratio)

    s_run = (
        ret20.clip(0, config.run_score_cap_pct) / config.run_score_cap_pct * config.run_score_points
    )
    s_stall = (config.flat_return_pct - ret5.abs()).clip(
        0, config.flat_return_pct
    ) / config.flat_return_pct * config.flat_return_score_points + (
        config.flat_range_ratio - range_ratio
    ).clip(
        0, config.range_ratio_score_cap
    ) / config.range_ratio_score_cap * config.flat_range_score_points
    s_momentum = np.where(
        (rsi_before >= config.momentum_rsi_minimum)
        & (rsi_before - rsi_now >= config.momentum_rsi_drop),
        config.momentum_drop_points,
        np.where(rsi_now >= config.momentum_rsi_minimum, config.momentum_level_points, 0),
    )
    s_distribution = (vol_ratio <= config.volume_ratio_maximum).astype(
        float
    ) * config.low_volume_points + (upper_wick >= config.upper_wick_minimum).astype(
        float
    ) * config.upper_wick_points
    score = (s_run.fillna(0) + s_stall.fillna(0) + s_momentum + s_distribution).clip(
        0, config.score_maximum
    )

    return pd.DataFrame(
        {
            "ts": df["ts"],
            "close": c,
            "ret20": ret20,
            "ext50": ext50,
            "ret5": ret5,
            "off_high": off_high,
            "range_ratio": range_ratio,
            "rsi": rsi_now,
            "rsi_before": rsi_before,
            "vol_ratio": vol_ratio,
            "upper_wick": upper_wick,
            "gate": ran_up & at_top & stalled,
            "score": score,
            "flag": ran_up & at_top & stalled & (score >= config.score_threshold),
        }
    )
