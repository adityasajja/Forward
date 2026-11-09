"""Momentum scanner over liquid NSE names using real IIFL candles.

Shared by the API (`GET /scan`) and any future CLI hook, so dashboard
signals and research math can't drift apart.
"""

from __future__ import annotations

import pandas as pd

from forward.strategy.indicators import atr, crossover, rsi, sma


def score_frame(symbol: str, df: pd.DataFrame) -> dict:
    """Momentum metrics for one OHLCV frame (ts/open/high/low/close/volume)."""
    df = df.sort_values("ts").reset_index(drop=True)
    if len(df) < 60:
        raise ValueError(f"only {len(df)} bars — need 60+")
    c = df["close"]
    last = float(c.iloc[-1])
    prev = float(c.iloc[-2])
    day_chg_pct = round((last / prev - 1) * 100, 2)
    ret_1m = float(c.iloc[-1] / c.iloc[-22] - 1) * 100
    hi = float(df["high"].tail(63).max())
    vs_high = (last / hi - 1) * 100
    s20, s50 = sma(c, 20), sma(c, 50)
    vol_ratio = float(df["volume"].iloc[-1] / df["volume"].tail(21).mean())
    trend = (
        "UP"
        if last > float(s20.iloc[-1]) > float(s50.iloc[-1])
        else ("DOWN" if last < float(s20.iloc[-1]) else "MIXED")
    )
    return {
        "symbol": symbol,
        "last": round(last, 1),
        "day_chg_pct": day_chg_pct,
        "day_high": round(float(df["high"].iloc[-1]), 1),
        "day_low": round(float(df["low"].iloc[-1]), 1),
        "day_value_lakh": round(float(last * df["volume"].iloc[-1] / 1e5), 1),
        "ret_1m": round(ret_1m, 1),
        "vs_high": round(vs_high, 1),
        "trend": trend,
        "rsi": round(float(rsi(c).iloc[-1]), 1),
        "vol_x": round(vol_ratio, 1),
        "atr_pct": round(float(atr(df["high"], df["low"], c).iloc[-1] / last * 100), 1),
        "gold_cross_5d": bool(crossover(s20, s50).tail(5).any()),
        "breakout": bool(vs_high > -2.0 and vol_ratio > 1.5),
        "score": round(ret_1m + vs_high, 1),
        "bars": len(df),
    }
