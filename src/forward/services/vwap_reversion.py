"""Persistent paper-only VWAP lifecycle: causal bars, quotes, budgets and IOC OMS."""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from forward.core.calendar_ports import MarketCalendarPort
from forward.core.live_market_data_ports import MarketDataPort
from forward.core.market_tracking_ports import VwapLiquidityPort
from forward.services.paper_intents import place_ioc
from forward.strategy.mean_reversion import IST, plan, signal


def quote(
    feed: MarketDataPort,
    symbol: str,
    now: datetime,
    *,
    max_age_seconds: float,
) -> dict[str, Any] | None:
    """Return a fresh, valid two-sided quote for one symbol.

    Args:
        feed: Live market-data port.
        symbol: Exact symbol subscribed by the runner.
        now: Current market time used to check quote age.

    Returns:
        Bid, ask, and quote timestamps, or ``None`` when the quote is unusable.
    """
    tick = feed.latest_tick(symbol)
    if tick is None:
        return None
    try:
        ts = datetime.fromisoformat(tick.ts)
        ts = ts.replace(tzinfo=IST) if ts.tzinfo is None else ts.astimezone(IST)
        recv = datetime.fromtimestamp(tick.epoch, IST)
        bid, ask = float(tick.best_bid or 0), float(tick.best_ask or 0)
        if (not all(math.isfinite(x) for x in [bid, ask]) or not 0 < bid <= ask
                or ts.date() != now.date() or recv.date() != now.date()
                or any(
                    not 0 <= (now - tick_time).total_seconds() <= max_age_seconds
                    for tick_time in [ts, recv]
                )):
            return None
        return {"bid": bid, "ask": ask, "exchange_time": ts, "received_time": recv,
                "previous_close": float(tick.close or 0)}
    except (TypeError, ValueError, OverflowError, AttributeError):
        return None


def evaluate(
    loop,
    now: datetime,
    market_data: MarketDataPort,
    liquidity_data: VwapLiquidityPort,
    calendar: MarketCalendarPort,
) -> list[dict[str, Any]]:
    """Evaluate and execute one paper-only VWAP strategy pass.

    Args:
        loop: Deployment loop with its orders, state, and portfolio.
        now: Current market time.
        market_data: Live quotes and completed session bars.
        liquidity_data: Prior-session liquidity measurements.
        calendar: Exchange calendar used to guard evaluation outside the session.

    Returns:
        Orders created during this pass.
    """
    now = now.astimezone(IST) if now.tzinfo else now.replace(tzinfo=IST)
    loop.last_strategy_evaluation = now
    if str(loop.row.get("mode")).upper() != "PAPER":
        raise ValueError("VWAP execution is paper-only")
    parameters = loop.config.vwap_parameters
    key = f"paper.vwap_reversion.{loop.deployment_id}"
    if not hasattr(loop, "_vwap_state"):
        loop._vwap_state = loop.state_storage.get(key) or {}
    state = loop._vwap_state
    feed = market_data
    portfolio = loop._portfolio()
    if state.get("day") != now.date().isoformat():
        state.update(day=now.date().isoformat(), session_equity=float(portfolio.equity),
                     attempts={}, candidates={}, bars={}, last_boundary=None, loss_gate=False)
    state.setdefault("plans", {})
    state.setdefault("intents", {})
    state.setdefault("entry_reservations", {})

    def save():
        loop.state_storage.set(
            key, state, actor=loop.user_id, reason="VWAP paper lifecycle"
        )

    def report(reason, orders=None):
        loop.skipped_reason = reason
        loop.last_pass = {"at": now.isoformat(), "state": loop.state, "skipped_reason": reason,
                          "orders": len(orders or []), "vwap_reversion": {
                              "attempts_today": len(state["attempts"]), "loss_gate": state["loss_gate"],
                              "candidates": len(state["candidates"]), "bar_source": "observed_live_quotes",
                              "last_boundary": state["last_boundary"], "skip_counts": state.get("skip_counts", {})}}
        return orders or []

    if not calendar.is_market_open(now):
        loop.state = "WAITING_FOR_MARKET"
        return report("Market closed; next complete session required")
    loop.state = "RUNNING"
    pending = False
    reservations_changed = False
    for token, reservation in list(state["entry_reservations"].items()):
        intent = loop.orders.get_by_idempotency_key(reservation["key"])
        if intent:
            order = loop.orders.get(intent["order_id"], loop.user_id)
            if order["status"] in {"ACKNOWLEDGED", "PARTIALLY_FILLED"}:
                loop.orders.request_cancel(order["order_id"], loop.user_id, reason="Recover IOC remainder")
                loop.orders.confirm_cancel(order["order_id"], loop.user_id, raw={"reason": "Recover IOC remainder"})
            elif order["status"] not in {"FILLED", "REJECTED", "CANCELLED", "EXPIRED"}:
                pending = True
                continue
        # No intent means crash before submission: consume the attempt, do not retry.
        state["entry_reservations"].pop(token)
        reservations_changed = True
    if reservations_changed:
        save()
    positions = {s: p for s, p in portfolio.positions.items() if not p.is_flat}
    quotes = {
        symbol: quote(
            feed,
            symbol,
            now,
            max_age_seconds=parameters["max_quote_age_seconds"],
        )
        for symbol in positions
    }
    marks_complete = all(q is not None for q in quotes.values())
    if marks_complete:
        equity = float(portfolio.cash)+sum(p.quantity*quotes[s]["bid"] for s, p in positions.items())
        if (
            equity <= float(state["session_equity"])
            * (1 - parameters["max_daily_loss_pct"])
            and not state["loss_gate"]
        ):
            state["loss_gate"] = True
            save()
    else:
        equity = float(portfolio.equity)

    actions = []
    # Process every open position before considering new entries.
    for symbol, position in positions.items():
        q = quotes[symbol]
        if q is None:
            continue
        record = state["plans"].get(symbol)
        if record is None:
            # A position with missing metadata is closed, never guessed into a new plan.
            reason = "Missing durable exit plan"
        else:
            stop = float(position.avg_price)-record["atr"]
            deadline = datetime.fromisoformat(record["first_fill_at"])+timedelta(
                minutes=parameters["max_hold_minutes"]
            )
            from forward.config.costs import indian_intraday_costs
            from forward.core.enums import Side
            from forward.core.instrument_models import Instrument
            info = liquidity_data.liquidity(date.fromisoformat(state["day"])).get(symbol)
            adv = info["adv"] if info else None
            if adv:
                impact = (
                    parameters["impact_multiplier"]
                    * math.sqrt(abs(position.quantity) / adv)
                    / parameters["impact_scale_bps"]
                )
                entry = float(position.avg_price)
                fees = indian_intraday_costs()
                inst = Instrument(symbol=symbol, exchange="NSEEQ")
                qty = abs(position.quantity)
                target_exit = (
                    record["target"] * parameters["target_exit_factor"] * (1-impact)
                )
                stop_exit = stop * parameters["target_exit_factor"] * (1-impact)
                entry_fee = fees.compute(qty, entry, inst, Side.BUY)
                net_gain = qty*(target_exit-entry)-entry_fee-fees.compute(qty, target_exit, inst, Side.SELL)
                net_loss = qty*(entry-stop_exit)+entry_fee+fees.compute(qty, stop_exit, inst, Side.SELL)
                geometry_bad = (
                    net_gain < qty * entry * parameters["min_net_gain_pct"]
                    or net_gain / net_loss < parameters["min_reward_risk"]
                )
            else:
                geometry_bad = True
            reason = record.get("exit_reason") or ("Invalid fill geometry" if geometry_bad else "Daily loss gate" if state["loss_gate"]
                      else "Prior session unresolved position" if record["day"] != state["day"]
                      else "ATR stop" if q["bid"] <= stop else "Frozen VWAP target" if q["bid"] >= record["target"]
                      else "Time exit" if now >= deadline or now.strftime("%H:%M") >= parameters["square_off_time"] else None)
        if reason:
            if record is not None and record.get("exit_reason") != reason:
                record["exit_reason"] = reason
                save()
            token = f"exit:{symbol}:{record['day'] if record else state['day']}"
            result = place_ioc(loop, state, save, token=token, symbol=symbol, side="SELL" if position.quantity > 0 else "BUY",
                               quantity=abs(position.quantity), now=now, reason=reason, retry=True,
                               limit_price=(
                                   q["bid"] * (1 - parameters["exit_price_pad_pct"])
                                   if position.quantity > 0
                                   else q["ask"] * (1 + parameters["exit_price_pad_pct"])
                               ))
            if result:
                actions.append(result)
    if actions:
        return report("Exit intents processed before entries", actions)
    if not marks_complete:
        loop.state = "WAITING_FOR_TICKS"
        return report("Fresh bid/ask missing for held position; entries disabled")
    if pending:
        return report("Unknown entry submission; reserved risk blocks new entries")
    if state["loss_gate"] or len(state["attempts"]) >= parameters["max_entry_attempts"]:
        return report("Daily loss gate" if state["loss_gate"] else "Three daily entry attempts used")
    if any(r.get("day") != state["day"] for s, r in state["plans"].items() if s in positions):
        return report("Unresolved prior-session position blocks entries")
    boundary = now.replace(minute=now.minute//5*5, second=0, microsecond=0).isoformat()
    if state["last_boundary"] != boundary:
        snapshot = feed.observed_bars(now)
        if snapshot.get("day") != state["day"]:
            return report("No complete live session bars; no historical substitution")
        for symbol, rows in snapshot["bars"].items():
            merged = {b["ts"]: b for b in state["bars"].get(symbol, [])}
            for row in rows:
                merged.setdefault(row["ts"], row)
            state["bars"][symbol] = sorted(merged.values(), key=lambda b: b["ts"])
        index_rows = state["bars"].get("NIFTY 50", [])
        index = pd.Series({pd.Timestamp(r["ts"])+pd.Timedelta(minutes=5): r["close"] for r in index_rows}, dtype=float)
        candidates, counts = {}, {}
        for symbol in loop.config.symbols:
            rows = state["bars"].get(symbol, [])
            if not rows:
                counts["no_session_bars"] = counts.get("no_session_bars", 0)+1
                continue
            frame = pd.DataFrame(rows).set_index("ts")
            decision = signal(frame, index, now=now, parameters=parameters)
            reason = decision["reason"]
            counts[reason] = counts.get(reason, 0)+1
            if decision["status"] == "candidate" and decision["signal_time"] == boundary:
                candidates[symbol] = decision
        state.update(candidates=candidates, last_boundary=boundary, skip_counts=counts)
        save()
    if (
        len(positions) >= parameters["max_open_positions"]
        or not parameters["entry_start"] <= now.strftime("%H:%M") <= parameters["entry_end"]
    ):
        return report(
            "Two position slots occupied"
            if len(positions) >= parameters["max_open_positions"]
            else "Signals are outside the configured entry window"
        )
    liq = liquidity_data.liquidity(date.fromisoformat(state["day"]))
    for symbol, candidate in sorted(state["candidates"].items(), key=lambda item: (item[1]["z"], item[0])):
        if symbol in positions or symbol in state["attempts"]:
            continue
        q = quote(
            feed,
            symbol,
            now,
            max_age_seconds=parameters["max_quote_age_seconds"],
        )
        info = liq.get(symbol)
        if q is None or info is None:
            continue
        # Conservative corporate-action/price-continuity proxy, not a verified CA feed.
        if (
            not q["previous_close"]
            or abs(q["previous_close"] / info["previous_close"] - 1)
            > parameters["previous_close_gap_limit_pct"]
        ):
            continue
        order_plan = plan(candidate, symbol=symbol, now=now, equity=equity, available_cash=portfolio.cash,
                          adv20=info["adv"], average_value20=info["value"],
                          parameters=parameters,
                          **{k: q[k] for k in ["bid", "ask", "exchange_time", "received_time"]})
        if order_plan["status"] != "planned":
            continue
        exposure = sum(abs(p.quantity)*quotes[s]["ask"] for s, p in positions.items())
        risk = sum(state["plans"].get(s, {}).get("planned_net_loss", equity) for s in positions)
        if (
            exposure + order_plan["quantity"] * order_plan["expected_entry"]
            > equity * parameters["max_book_exposure_pct"]
            or risk + order_plan["planned_net_loss"]
            > equity * parameters["max_planned_loss_pct"]
        ):
            continue
        token = f"entry:{state['day']}:{symbol}:{candidate['signal_time']}"
        # Reserve count, risk and actual-fill exit metadata BEFORE OMS submission.
        from forward.execution.oms import idempotency_key_for
        intent_key = idempotency_key_for(user_id=loop.user_id, strategy_id=loop.row.get("strategy_id"),
            strategy_version=loop.row.get("strategy_version"), signal_id=f"{loop.deployment_id}:{token}:0",
            symbol=symbol, side="BUY", leg_index=0)
        state["attempts"][symbol] = {"token": token, "key": intent_key, "at": now.isoformat()}
        state["entry_reservations"][token] = {"key": intent_key}
        state["plans"][symbol] = {**order_plan, "atr": candidate["atr"], "day": state["day"], "first_fill_at": now.isoformat()}
        save()
        result = place_ioc(loop, state, save, token=token, symbol=symbol, side="BUY", quantity=order_plan["quantity"],
                           now=now, reason="Completed-bar VWAP recovery", limit_price=order_plan["entry_limit"])
        # Reconcile actual entry; provisional durable metadata already covers a crash.
        actual = loop._portfolio().positions.get(symbol)
        if actual and not actual.is_flat:
            entry = float(actual.avg_price)
            gain = candidate["target"] * parameters["target_exit_factor"] - entry
            loss = candidate["atr"] + entry * parameters["exit_price_pad_pct"]
            state["plans"][symbol]["invalid_fill"] = (
                gain / entry < parameters["min_net_gain_pct"]
                or gain / loss < parameters["min_reward_risk"]
            )
            save()
        return report("Paper IOC entry attempted through risk/OMS", [result] if result else [])
    return report("No candidate passed fresh quote, liquidity, cost and portfolio gates")
