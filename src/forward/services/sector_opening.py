"""Daily state machine for the paper-only Nifty opening-sector hypothesis."""
from __future__ import annotations

import math
from datetime import datetime

from forward.core.calendar_ports import MarketCalendarPort
from forward.core.live_market_data_ports import MarketDataPort
from forward.core.session import IST
from forward.strategy.definition import parse_definition
from forward.strategy.strategies.sector_opening import validate_definition


def choose(universe: dict, snapshot: dict) -> dict:
    """Require complete coverage before ranking. Ties are alphabetical."""
    indices, stocks = snapshot["indices"], snapshot["stocks"]
    required = {universe["index"]["name"], *universe["sectors"]}
    members = {s for sec in universe["sectors"].values() for s in sec["members"]}
    missing = sorted(required - indices.keys()) + sorted(members - stocks.keys())
    if missing:
        return {"status": "collecting", "reason": f"Missing {len(missing)} opening observations", "missing": missing}
    def change(q, field):
        p, prev = float(q[field]), float(q["prev_close"])
        if not all(math.isfinite(x) and x > 0 for x in (p, prev)):
            raise ValueError("Invalid opening observation")
        return p / prev - 1
    nifty = change(indices[universe["index"]["name"]], "ltp")
    if nifty == 0:
        return {"status": "skipped", "reason": "Nifty is flat"}
    sign = 1 if nifty > 0 else -1
    sectors = sorted(((sign * change(indices[n], "ltp"), n) for n in universe["sectors"]), key=lambda x: (-x[0], x[1]))
    strength, sector = sectors[0]
    if strength <= 0:
        return {"status": "skipped", "reason": "No sector agrees with Nifty direction"}
    ranked = sorted(((sign * change(stocks[s], "open"), s) for s in universe["sectors"][sector]["members"]), key=lambda x: (-x[0], x[1]))
    strength, symbol = ranked[0]
    if strength <= 0:
        return {"status": "skipped", "reason": "No stock agrees with Nifty direction in the strongest sector"}
    return {"status": "selected", "symbol": symbol, "sector": sector,
            "side": "BUY" if sign > 0 else "SELL", "nifty_change": nifty,
            "stock_open_change": sign * strength, "reason": "09:15 opening selection frozen"}


def signal_key(loop, symbol: str) -> str:
    """Return a signal key that allows one opening entry and one exit per day.

    Args:
        loop: Deployment loop with the current evaluation and phase.
        symbol: Selected instrument.

    Returns:
        A key that changes by symbol, day, and entry or exit phase.
    """
    now = getattr(loop, "_evaluation_time", None) or datetime.now(IST)
    phase = getattr(loop, "_sector_phase", "entry")
    return f"{symbol}:{now.date().isoformat()}:{phase}"


def evaluate(
    loop,
    now: datetime,
    market_data: MarketDataPort,
    calendar: MarketCalendarPort,
) -> list[dict]:
    """Run one opening-sector paper evaluation.

    Args:
        loop: Deployment loop with its order, state, and portfolio access.
        now: Current market time.
        market_data: Live opening snapshot and quote source.
        calendar: Exchange calendar used to guard market-session actions.

    Returns:
        Orders created during this evaluation.
    """
    now = now.astimezone(IST) if now.tzinfo else now.replace(tzinfo=IST)
    loop.last_strategy_evaluation = now
    def report(reason, orders=None):
        loop.skipped_reason = reason
        loop.last_pass = {"at": now.isoformat(), "state": loop.state,
                          "skipped_reason": reason, "orders": len(orders or []),
                          "sector_opening": {k: v for k, v in (getattr(loop, "_sector_state", (None, {}))[1]).items() if k != "snapshot"}}
        return orders or []
    if str(loop.row.get("mode", "")).upper() != "PAPER":
        loop.state = "ERROR"
        return report("Opening-sector execution is paper-only")
    if not calendar.is_market_open(now):
        loop.state = "WAITING_FOR_MARKET"
        return report("Market closed")
    definition, _ = parse_definition(loop.row.get("definition"))
    universe = validate_definition(definition)
    key = f"paper.sector_opening.{loop.deployment_id}.{now.date().isoformat()}"
    cached = getattr(loop, "_sector_state", None)
    if not cached or cached[0] != key:
        state = loop.state_storage.get(key) or {
            "status": "collecting",
            "snapshot": {"indices": {}, "stocks": {}},
        }
        loop._sector_state = (key, state)
    else:
        state = cached[1]
    def save():
        loop.state_storage.set(
            key, state, actor=loop.user_id, reason="Opening-sector paper execution"
        )
    clock = now.strftime("%H:%M")
    exception = state.get("entry_exception") or {}
    try:
        expires = datetime.fromisoformat(exception["expires_at"])
        approved = datetime.fromisoformat(exception["approved_at"])
        late_allowed = (exception.get("day") == now.date().isoformat()
                        and approved <= now <= expires and bool(exception.get("authorized_by")))
    except (KeyError, ValueError, TypeError):
        late_allowed = False
    loop.state = "RUNNING"
    def fresh_tick(symbol, minute=None):
        tick = market_data.latest_tick(symbol)
        if tick is None:
            return False
        try:
            ts = datetime.fromisoformat(tick.ts)
            ts = ts.replace(tzinfo=IST) if ts.tzinfo is None else ts.astimezone(IST)
            return (ts.date() == now.date() and 0 <= (now-ts).total_seconds() <= 15
                    and 0 <= now.timestamp()-tick.epoch <= 15
                    and (minute is None or ts.strftime("%H:%M") == minute))
        except (TypeError, ValueError, AttributeError):
            return False
    # Manage any real ledger position first, even if a previous day's square-off was unresolved.
    positions = [(symbol, p) for symbol, p in loop._portfolio().positions.items() if not p.is_flat]
    if positions:
        symbol, p = positions[0]
        price, ts, age = loop._price_with_meta(symbol, now=now)
        if price is None or ts is None or age is None or not 0 <= age <= 15 or not fresh_tick(symbol):
            loop.state = "WAITING_FOR_TICKS"
            return report("Fresh quote required to exit; position remains open")
        pnl = (price / float(p.avg_price) - 1) * (1 if p.quantity > 0 else -1)
        reason = state.get("exit_reason") or ("1% stop" if pnl <= -0.01 + 1e-12 else "1% target" if pnl >= 0.01 - 1e-12 else "15:20 square-off" if clock >= "15:20" else None)
        if reason:
            if not state.get("exit_reason"):
                state["exit_reason"] = reason
                save()
            loop._sector_phase = "exit"
            from forward.services.paper_intents import place_ioc
            result = place_ioc(loop, state, save, token=f"sector-exit:{symbol}:{now.date().isoformat()}",
                               symbol=symbol, side="SELL" if p.quantity > 0 else "BUY",
                               quantity=abs(p.quantity), now=now, reason=reason, retry=True)
            return report(reason, [result] if result else [])
        return report("Holding; waiting for ±1% or 15:20")
    if state.get("attempted"):
        return report("Daily entry already attempted; no re-entry")
    if state["status"] == "collecting":
        if clock < "09:15":
            return report("Waiting for 09:15")
        if clock > "09:15":
            state.update(status="skipped", reason="Incomplete or missed 09:15 snapshot")
            save()
        else:
            snapshot = market_data.opening_snapshot(now.date().isoformat())
            changed = False
            for group in ("indices", "stocks"):
                for name, q in snapshot[group].items():
                    if name not in state["snapshot"][group]:
                        state["snapshot"][group][name] = q
                        changed = True
            decision = choose(universe, state["snapshot"])
            state.update(decision)
            if changed or decision["status"] != "collecting":
                state["selected_at"] = now.isoformat()
                save()
    if state["status"] != "selected":
        return report(state.get("reason", "Waiting for opening observations"))
    loop.last_signal = {k: v for k, v in state.items() if k != "snapshot"}
    if clock < "09:20":
        return report("Selection frozen; waiting for 09:20")
    if clock > "09:20" and not late_allowed:
        state.update(status="skipped", reason="09:20 entry window missed")
        save()
        return report(state["reason"])
    symbol = state["symbol"]
    price, ts, age = loop._price_with_meta(symbol, now=now)
    # Explicit feed timestamp, not the generic venue's cached numeric fallback.
    if price is None or ts is None or age is None or not 0 <= age <= 15 or not fresh_tick(symbol, None if late_allowed else "09:20"):
        loop.state = "WAITING_FOR_TICKS"
        return report("Waiting for a fresh live tick for authorized late entry" if late_allowed else "Waiting for a fresh 09:20 live tick")
    equity = min(float(loop.row["capital"]), float(loop._portfolio().equity))
    quantity = math.floor(max(0, equity) * 0.98 / price)
    state.update(attempted=True, attempted_at=now.isoformat(), requested_quantity=quantity)
    save()  # Fail closed if a crash occurs between persistence and submission.
    if quantity < 1:
        return report("Insufficient capital for one whole share")
    loop._sector_phase = "entry"
    label = "User-authorized late PAPER entry; frozen 09:15 selection" if late_allowed else "09:20 frozen opening selection"
    result = loop._trade(symbol=symbol, side=state["side"], quantity=quantity,
                         reason=f"{label}: {state['sector']}; Nifty {state['nifty_change']:+.4%}")
    state["entry_order"] = result
    save()
    return report("Authorized late entry attempted through risk/OMS" if late_allowed else "09:20 entry attempted through risk/OMS", [result] if result else [])
