"""Alert evaluation: bulk quotes in, firings out.

One `marketquotes` call covers every armed rule, so a 50-symbol watchlist
costs a single API round-trip. Indicator rules (RSI/crosses) read the local
daily cache with today's live price appended as the forming bar.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import pandas as pd
from loguru import logger

from forward.alerts.models import AlertEvent, AlertRule
from forward.core.alert_ports import (
    AlertDeliveryPort,
    AlertHistoryPort,
    AlertQuote,
    AlertQuotePort,
    AlertStorePort,
)
from forward.core.session import IST, local_datetime
from forward.strategy.indicators import crossover, crossunder, rsi, sma


def evaluate(
    rule: AlertRule, quote: AlertQuote, frame: pd.DataFrame | None
) -> tuple[bool, str]:
    """Evaluate one alert against its latest quote and optional daily bars.

    Args:
        rule: Alert rule to evaluate.
        quote: Latest quote fields for the rule's symbol.
        frame: Daily context for indicator rules, or None for price rules.

    Returns:
        Whether the rule fired and a message explaining the result.
    """
    ltp = quote["ltp"]
    if rule.kind == "price_above":
        return ltp >= rule.threshold, f"{rule.symbol} at {ltp} (≥ {rule.threshold})"
    if rule.kind == "price_below":
        return ltp <= rule.threshold, f"{rule.symbol} at {ltp} (≤ {rule.threshold})"
    if rule.kind == "day_drop_pct":
        return quote["day_chg"] <= -abs(rule.threshold), \
            f"{rule.symbol} down {quote['day_chg']:.1f}% today @ {ltp}"
    if rule.kind == "day_gain_pct":
        return quote["day_chg"] >= abs(rule.threshold), \
            f"{rule.symbol} up {quote['day_chg']:.1f}% today @ {ltp}"
    if frame is None or len(frame) < 30:
        return False, "no daily context"
    c = frame["close"]
    if rule.kind in ("rsi_below", "rsi_above"):
        v = float(rsi(c).iloc[-1])
        comparison = v <= rule.threshold if rule.kind == "rsi_below" else v >= rule.threshold
        operator = "≤" if rule.kind == "rsi_below" else "≥"
        return comparison, f"{rule.symbol} RSI {v:.0f} ({operator} {rule.threshold}) @ {ltp}"
    s20, s50 = sma(c, 20), sma(c, 50)
    if rule.kind in ("sma_cross_up", "sma_cross_down"):
        cross = crossover(s20, s50) if rule.kind == "sma_cross_up" else crossunder(s20, s50)
        hit = bool(cross.tail(5).any())
        name = "golden" if rule.kind == "sma_cross_up" else "death"
        return hit, f"{rule.symbol} {name} cross near {ltp}"
    raise ValueError(f"unknown alert rule kind: {rule.kind}")


def check(
    store: AlertStorePort,
    quotes_port: AlertQuotePort,
    history: AlertHistoryPort,
    channels: list[AlertDeliveryPort],
    now: datetime | None = None,
) -> list[AlertEvent]:
    """Evaluate armed rules through market, history, and delivery ports.

    Args:
        store: Alert rule and event storage.
        quotes_port: Bulk quote reader.
        history: Daily context reader.
        channels: Notification channels, in priority order.
        now: Evaluation time, or the current local time.

    Returns:
        Events created by rules that fired.
    """
    now = local_datetime(now) if now is not None else datetime.now(IST).replace(tzinfo=None)
    rules = [r for r in store.rules() if r.armed]
    if not rules:
        return []
    requested_symbols = {rule.symbol for rule in rules}
    quotes = quotes_port.quotes([(r.symbol, r.exchange) for r in rules])
    missing_symbols = requested_symbols - quotes.keys()
    if missing_symbols:
        raise RuntimeError(f"alert quote response is missing symbols: {sorted(missing_symbols)}")
    fired: list[AlertEvent] = []
    for rule in rules:
        if rule.last_fired_at and now - rule.last_fired_at < timedelta(minutes=rule.cooldown_min):
            continue
        quote = quotes[rule.symbol]
        if not math.isfinite(quote["ltp"]) or quote["ltp"] <= 0:
            raise ValueError(f"alert quote for {rule.symbol} must have a positive LTP")
        frame = history.context(rule.symbol, quote["bar"]) \
            if rule.kind.startswith(("rsi", "sma")) else None
        hit, message = evaluate(rule, quote, frame)
        if not hit:
            continue
        sent_on = "none"
        ok = False
        for ch in channels:
            if ch.send(f"Forward alert: {rule.display}", message):
                sent_on, ok = ch.name, True
                break
        event = AlertEvent(rule_id=rule.id, rule=rule.display,
                           message=message, channel=sent_on, ok=ok)
        store.log(event)
        rule.last_fired_at = now
        store.upsert(rule)
        logger.warning("alert fired [{}] {}", sent_on, message)
        fired.append(event)
    return fired
