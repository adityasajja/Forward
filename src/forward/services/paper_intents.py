"""Place durable IOC intents for paper-only strategy profiles."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from forward.execution.oms import OrderDraft, idempotency_key_for


def place_ioc(
    loop: Any,
    state: dict[str, Any],
    save: Callable[[], None],
    *,
    token: str,
    symbol: str,
    side: str,
    quantity: float,
    now: datetime,
    reason: str,
    limit_price: float | None = None,
    retry: bool = False,
) -> dict[str, Any] | None:
    """Submit one durable paper IOC order and reconcile its final state.

    Args:
        loop: Deployment loop that provides order and execution services.
        state: Saved strategy state that owns the intent record.
        save: Callback that persists ``state`` before and after submission.
        token: Stable name for this order attempt.
        symbol: Exact listed instrument to trade.
        side: ``BUY`` or ``SELL``.
        quantity: Requested order quantity.
        now: Current time used to limit retries.
        reason: Human-readable reason stored with the order.
        limit_price: Limit for the IOC order, or ``None`` for a market order.
        retry: Whether a completed prior intent may be retried.

    Returns:
        Order details, or ``None`` when a retry is not yet allowed.

    Raises:
        ValueError: If the deployment is not in paper mode.
    """
    if str(loop.row.get("mode")).upper() != "PAPER":
        raise ValueError("Special strategy IOC intents are paper-only")

    intent = state.setdefault("intents", {}).setdefault(token, {"generation": 0})

    def intent_key() -> str:
        """Return the idempotency key for the current retry generation."""
        return idempotency_key_for(
            user_id=loop.user_id,
            strategy_id=loop.row.get("strategy_id"),
            strategy_version=loop.row.get("strategy_version"),
            signal_id=(
                f"{loop.deployment_id}:{token}:{intent['generation']}"
            ),
            symbol=symbol,
            side=side,
            leg_index=0,
        )

    existing = loop.orders.get_by_idempotency_key(intent_key())
    if retry:
        other_open_orders = [
            order
            for order in loop.orders.open_orders(
                loop.user_id, deployment_id=loop.deployment_id
            )
            if order["symbol"] == symbol
            and order["side"] == side
            and (not existing or order["order_id"] != existing["order_id"])
        ]
        if other_open_orders:
            order = other_open_orders[0]
            return {
                "order_id": order["order_id"],
                "status": order["status"],
                "duplicate": True,
            }

    if existing:
        order = loop.orders.get(existing["order_id"], loop.user_id)
        status = order["status"]
        if status in {"ACKNOWLEDGED", "PARTIALLY_FILLED"}:
            loop.orders.request_cancel(
                order["order_id"], loop.user_id, reason="Paper IOC remainder"
            )
            loop.orders.confirm_cancel(
                order["order_id"],
                loop.user_id,
                raw={"reason": "Paper IOC remainder"},
            )
            status = "CANCELLED"

        terminal_states = {"REJECTED", "CANCELLED", "EXPIRED", "FILLED"}
        if not retry or status not in terminal_states:
            return {
                "order_id": order["order_id"],
                "status": status,
                "duplicate": True,
            }
        if now.timestamp() < intent.get("retry_after", 0):
            return None
        intent["generation"] += 1

    intent["retry_after"] = now.timestamp() + 5
    save()
    draft = OrderDraft(
        user_id=loop.user_id,
        symbol=symbol,
        side=side,
        quantity=float(quantity),
        mode="PAPER",
        exchange=loop.config.exchange_of(symbol),
        product=loop.config.product,
        tif="IOC",
        order_type="LIMIT" if limit_price is not None else "MARKET",
        limit_price=limit_price,
        deployment_id=loop.deployment_id,
        strategy_id=loop.row.get("strategy_id"),
        strategy_version=loop.row.get("strategy_version"),
        signal_id=f"{loop.deployment_id}:{token}",
        signal_reason=reason,
    )
    result = loop.execution.place(draft, idempotency_key=intent_key())
    intent["order_id"] = result.order_id
    save()

    order = loop.orders.get(result.order_id, loop.user_id)
    if order["status"] in {"ACKNOWLEDGED", "PARTIALLY_FILLED"}:
        loop.orders.request_cancel(
            result.order_id, loop.user_id, reason="Paper IOC remainder"
        )
        loop.orders.confirm_cancel(
            result.order_id,
            loop.user_id,
            raw={"reason": "Paper IOC remainder"},
        )

    outcome = {
        "order_id": result.order_id,
        "status": result.status,
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "reason": reason,
    }
    loop.last_order = outcome
    loop._fold = None
    return outcome
