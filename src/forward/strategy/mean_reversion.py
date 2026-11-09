"""Offline causal VWAP hypothesis core; no registry, order submission or P&L claims."""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from forward.config.costs import indian_intraday_costs
from forward.core.enums import Side
from forward.core.instrument_models import Instrument
from forward.core.session import IST


def _ist(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize(IST) if ts.tzinfo is None else ts.tz_convert(IST)


def signal(
    bars: pd.DataFrame,
    nifty: pd.Series,
    *,
    now: datetime,
    parameters: dict[str, Any],
) -> dict:
    """Bars indexed by start timestamp; Nifty closes indexed by closing time.

    Ignore future/incomplete rows, require a contiguous current session. Session
    eligibility/corporate-action checks are the caller's explicit responsibility.
    """
    now = _ist(now)
    frame = bars.copy()
    frame.index = pd.DatetimeIndex([_ist(t) for t in frame.index])
    hour, minute = (int(part) for part in parameters["session_start"].split(":"))
    start = now.normalize() + pd.Timedelta(hours=hour, minutes=minute)
    bar_width = pd.Timedelta(minutes=parameters["bar_minutes"])
    frame = frame[(frame.index >= start) & (frame.index + bar_width <= now)].sort_index()
    if len(frame) < parameters["warmup_bars"]:
        return {"status": "skip", "reason": "warmup"}
    expected = pd.date_range(start, frame.index[-1], freq=bar_width)
    if not frame.index.equals(expected):
        return {"status": "skip", "reason": "missing_or_duplicate_bar"}
    values = frame[["open", "high", "low", "close", "volume"]].to_numpy(dtype=float)
    if (not np.isfinite(values).all() or (values[:, :4] <= 0).any()
            or (values[:, 4] < 0).any()
            or (frame.high < frame[["open", "close", "low"]].max(axis=1)).any()
            or (frame.low > frame[["open", "close", "high"]].min(axis=1)).any()):
        return {"status": "skip", "reason": "invalid_bar"}
    closed_at = frame.index[-1] + bar_width
    if not parameters["entry_start"] <= closed_at.strftime("%H:%M") <= parameters["entry_end"]:
        return {"status": "skip", "reason": "signal_window"}
    typical = (frame.high + frame.low + frame.close) / 3
    vwap = (typical * frame.volume).cumsum() / frame.volume.cumsum().replace(0, np.nan)
    residual = frame.close - vwap
    sigma = residual.shift(1).rolling(parameters["vwap_residual_window"]).std(ddof=1).iloc[-1]
    if not math.isfinite(sigma) or sigma <= 0:
        return {"status": "skip", "reason": "invalid_sigma"}
    z = float(residual.iloc[-1] / sigma)
    previous = frame.close.shift(1)
    tr = pd.concat([frame.high-frame.low, (frame.high-previous).abs(),
                    (frame.low-previous).abs()], axis=1).max(axis=1)
    atr = float(tr.rolling(parameters["atr_period"]).mean().iloc[-1])
    index = nifty.copy()
    index.index = pd.DatetimeIndex([_ist(t) for t in index.index])
    times = pd.date_range(
        closed_at - pd.Timedelta(minutes=parameters["index_move_lookback_minutes"]),
        closed_at,
        freq=bar_width,
    )
    if index.index.duplicated().any() or not times.isin(index.index).all():
        return {"status": "skip", "reason": "missing_index_bar"}
    index = index.loc[times].astype(float)
    if not np.isfinite(index).all() or (index <= 0).any():
        return {"status": "skip", "reason": "invalid_index_bar"}
    row = frame.iloc[-1]
    ref = float(vwap.iloc[-1])
    slope = abs(ref / vwap.iloc[-parameters["vwap_slope_bars"] - 1] - 1)
    index_move = abs(index.iloc[-1] / index.iloc[0] - 1)
    passes = (
        z <= -parameters["entry_z_score"]
        and row.close > row.open
        and row.close > frame.close.iloc[-2]
        and ref / row.close - 1 >= parameters["min_vwap_discount_pct"]
        and atr / row.close >= parameters["min_atr_pct"]
        and slope <= parameters["max_vwap_slope_pct"]
        and index_move <= parameters["max_index_move_pct"]
    )
    return {"status": "candidate" if passes else "skip", "reason": "qualified" if passes else "entry_filters",
            "signal_time": closed_at.isoformat(), "target": ref, "atr": atr, "z": z,
            "close": float(row.close), "vwap_slope": float(slope), "nifty_move": float(index_move)}


def plan(candidate: dict, *, symbol: str, now: datetime, bid: float, ask: float,
         exchange_time: datetime, received_time: datetime, equity: float,
         available_cash: float, adv20: float, average_value20: float,
         parameters: dict[str, Any]) -> dict:
    """Plan a single long candidate; portfolio/session/OMS gates are external.

    Entry uses ask plus 10bps and impact; exit uses adverse 10bps plus impact.
    Expected loss is a sizing estimate, not a guaranteed maximum stop loss.
    """
    def skip(reason):
        return {"status": "skip", "reason": reason}
    if candidate.get("status") != "candidate":
        return skip("not_candidate")
    now, when = _ist(now), _ist(candidate["signal_time"])
    if not 0 < (now-when).total_seconds() <= parameters["max_signal_age_seconds"]:
        return skip("entry_window")
    if any(
        not 0 <= (now - _ist(t)).total_seconds() <= parameters["max_quote_age_seconds"]
        for t in [exchange_time, received_time]
    ):
        return skip("stale_quote")
    if any(_ist(t) <= when for t in [exchange_time, received_time]):
        return skip("quote_precedes_signal")
    numbers = [bid, ask, equity, available_cash, adv20, average_value20,
               candidate["target"], candidate["atr"]]
    if not all(math.isfinite(x) and x > 0 for x in numbers) or bid > ask:
        return skip("invalid_inputs")
    if (ask-bid)/((ask+bid)/2) > parameters["max_spread_pct"]:
        return skip("wide_spread")
    if average_value20 < parameters["min_average_value"]:
        return skip("illiquid")
    cap = math.floor(
        min(
            equity * parameters["max_single_name_equity_pct"] / ask,
            available_cash * parameters["cash_reserve_pct"] / ask,
            adv20 * parameters["max_adv_participation"],
        )
    )
    if cap < 1:
        return skip("insufficient_capital")
    fees = indian_intraday_costs()
    instrument = Instrument(symbol=symbol, exchange="NSEEQ")

    def amounts(qty):
        impact = (
            parameters["impact_multiplier"]
            * math.sqrt(qty / adv20)
            / parameters["impact_scale_bps"]
        )
        entry = ask * (1 + parameters["entry_price_pad_pct"]) * (1 + impact)
        stop = entry-candidate["atr"]
        stop_exit = stop * parameters["target_exit_factor"] * (1-impact)
        target_exit = candidate["target"] * parameters["target_exit_factor"] * (1-impact)
        entry_fee = fees.compute(qty, entry, instrument, Side.BUY)
        loss = qty*(entry-stop_exit)+entry_fee+fees.compute(qty, stop_exit, instrument, Side.SELL)
        gain = qty*(target_exit-entry)-entry_fee-fees.compute(qty, target_exit, instrument, Side.SELL)
        return entry, stop, loss, gain

    # All sizing constraints are monotonic in quantity under this cost model.
    low, high = 0, cap
    while low < high:
        middle = (low+high+1)//2
        entry, stop, loss, _ = amounts(middle)
        buy_cost = middle*entry+fees.compute(middle, entry, instrument, Side.BUY)
        fits = (
            stop > 0
            and loss <= equity * parameters["max_position_loss_pct"]
            and middle * entry <= equity * parameters["max_single_name_equity_pct"]
            and buy_cost <= available_cash * parameters["cash_reserve_pct"]
            and entry <= ask * (1 + parameters["entry_limit_pad_pct"])
        )
        if fits:
            low = middle
        else:
            high = middle-1
    if not low:
        return skip("risk_or_execution_cap")
    entry, stop, loss, gain = amounts(low)
    if (
        gain <= 0
        or gain / (low * entry) < parameters["min_net_gain_pct"]
        or gain / loss < parameters["min_reward_risk"]
    ):
        return skip("insufficient_net_reward")
    return {"status": "planned", "symbol": symbol, "side": "BUY", "quantity": low,
            "expected_entry": entry,
            "entry_limit": ask * (1 + parameters["entry_limit_pad_pct"]),
            "target": candidate["target"],
            "stop": stop, "expected_net_gain": gain, "planned_net_loss": loss,
            "net_reward_risk": gain/loss, "signal_time": when.isoformat(),
            "note": "Requires persistent session/portfolio gates and OMS adapter; not an order"}
