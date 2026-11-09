"""One-minute approximation of the paper engine; never submits orders.

Selection is shared with services.sector_opening.choose. OHLC execution is an
explicit approximation, not a reconstruction of quote reception or order-book fills.
"""
from __future__ import annotations

import math

import pandas as pd

from forward.config.costs import indian_intraday_costs
from forward.core.enums import Side
from forward.core.instrument_models import Instrument


def replay_path(path: pd.DataFrame, side: str, equity: float = 500_000,
                slippage_bps: float = 5, *, target_first: bool = False,
                adv: float | None = None) -> dict:
    if side not in {"BUY", "SELL"} or not 0 <= slippage_bps < 100:
        raise ValueError("Invalid replay direction or slippage")
    if path.empty:
        return {"status": "missing_entry"}
    path = path.sort_values("ts")
    start = pd.Timestamp(path.iloc[0].ts).normalize() + pd.Timedelta(hours=9, minutes=20)
    if pd.Timestamp(path.iloc[0].ts) != start:
        return {"status": "missing_entry"}
    sign = 1 if side == "BUY" else -1
    slip = slippage_bps / 10_000
    reference = float(path.iloc[0].open)
    if not math.isfinite(reference) or reference <= 0:
        return {"status": "missing_entry", "reason": "invalid_entry_price"}
    qty = math.floor(max(0, min(equity, 500_000)) * .98 / reference)
    requested = qty
    if adv is not None and math.isfinite(adv) and adv > 0:
        qty = min(qty, max(1, math.floor(adv * .05)))
    else:
        adv = None
    if qty < 1:
        return {"status": "insufficient_capital"}
    impact = 100 * math.sqrt(qty / adv) / 10_000 if adv else 0
    entry = reference * (1 + sign * slip) * (1 + sign * impact)
    stop, target = entry * (1 - sign * .01), entry * (1 + sign * .01)
    result = {"entry": entry, "entry_time": str(start), "quantity": qty,
              "stop": stop, "target": target, "side": side,
              "adv": adv, "requested_quantity": requested, "impact_bps": impact*10_000}
    instrument = Instrument(symbol="REPLAY", exchange="NSEEQ")
    costs = indian_intraday_costs()
    for i, row in enumerate(path.itertuples()):
        expected = start + pd.Timedelta(minutes=i)
        if pd.Timestamp(row.ts) != expected:
            return {**result, "status": "unresolved", "reason": "missing_minute_before_exit", "missing_at": str(expected)}
        values = [float(row.open), float(row.high), float(row.low), float(row.close)]
        if (not all(math.isfinite(v) and v > 0 for v in values)
                or row.high < max(row.open, row.close, row.low)
                or row.low > min(row.open, row.close, row.high)):
            return {**result, "status": "unresolved", "reason": "invalid_bar", "missing_at": str(expected)}
        ambiguous = False
        if expected.strftime("%H:%M") == "15:20":
            raw, reason = float(row.open), "square_off"
        elif sign * (row.open - stop) <= 0:
            raw, reason = float(row.open), "stop_gap"
        elif sign * (row.open - target) >= 0:
            raw, reason = target, "target_gap"
        else:
            hit_stop = row.low <= stop if sign > 0 else row.high >= stop
            hit_target = row.high >= target if sign > 0 else row.low <= target
            ambiguous = bool(hit_stop and hit_target)
            if hit_target and (target_first or not hit_stop):
                raw, reason = target, "target_both_hit" if hit_stop else "target"
            elif hit_stop:
                raw, reason = stop, "stop_both_hit" if hit_target else "stop"
            else:
                continue
        exit_price = raw * (1 - sign * slip) * (1 - sign * impact)
        entry_side = Side.BUY if sign > 0 else Side.SELL
        exit_side = Side.SELL if sign > 0 else Side.BUY
        fees = (costs.compute(qty, entry, instrument, entry_side)
                + costs.compute(qty, exit_price, instrument, exit_side))
        gross = sign * qty * (exit_price-entry)
        return {**result, "status": "resolved", "exit": exit_price, "exit_time": str(expected),
                "reason": reason, "ambiguous": ambiguous, "gross_pnl": gross,
                "fees": fees, "net_pnl": gross-fees,
                "realised_price_return_pct": sign * (exit_price / entry - 1) * 100}
    return {**result, "status": "unresolved", "reason": "path_ended_before_exit"}


def wilson(wins: int, count: int) -> list[float] | None:
    if count == 0:
        return None
    z = 1.95996398454
    p = wins / count
    centre = (p+z*z/(2*count))/(1+z*z/count)
    half = z*math.sqrt(p*(1-p)/count+z*z/(4*count*count))/(1+z*z/count)
    return [100*(centre-half), 100*(centre+half)]
