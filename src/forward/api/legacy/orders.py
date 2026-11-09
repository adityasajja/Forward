"""Positions and the manual order endpoint."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from forward.api.deps import require_permission
from forward.api.legacy.access import (
    live_broker,
    order_principal,
    require_live_execution,
    require_order_permission,
)
from forward.auth.rbac import Permission

router = APIRouter()


class OrderRequest(BaseModel):
    symbol: str
    exchange: str = "NSEEQ"
    quantity: int  # signed: negative = sell
    order_type: str = "MARKET"
    price: float | None = None
    product: str | None = None
    tag: str | None = None
    #: Made by the client for each confirmed order. Sending the same id twice (a double click, a
    #: retried request) returns the first order instead of placing a second one.
    client_order_id: str | None = Field(default=None, min_length=8, max_length=64)


@router.get(
    "/positions",
    dependencies=[Depends(require_permission(Permission.ORDER_READ))],
)
def positions(http_request: Request) -> list[dict[str, Any]]:
    order_principal(http_request)
    from forward.wiring.broker_data import build_broker_positions_service

    return build_broker_positions_service(live_broker()).read()


@router.post("/orders")
def place_order(request: OrderRequest, http_request: Request) -> dict[str, Any]:
    """Place a manual order.

    Rewired onto the OMS on 2026-09-14. Before this, the route called
    ``broker.place_order()`` directly: no risk check, no idempotency, no event log,
    and — because it built the ``Order`` itself — no single place where an order's
    shape was decided. It now goes through the same
    :class:`~forward.services.execution.ExecutionService` as every other path.

    **Requires a platform account.** This is a deliberate tightening. An order
    needs an owner (``orders.user_id`` is a non-null foreign key), and an
    unattributed order is one nobody can be asked about. Public market-data
    reads follow the configured authentication policy; account and order data
    require the matching read permission.
    """
    from forward.composition import build_live_execution_service
    from forward.core.errors import VenueError

    principal = order_principal(http_request)
    require_order_permission(principal)
    require_live_execution("Manual order")

    broker = live_broker()
    side = "BUY" if request.quantity > 0 else "SELL"
    service = build_live_execution_service(broker, user_id=principal.user_id)
    try:
        placed = service.place_manual(
            user_id=principal.user_id,
            symbol=request.symbol,
            signed_quantity=request.quantity,
            exchange=request.exchange,
            order_type=request.order_type,
            price=request.price,
            product=request.product,
            tag=request.tag,
            client_order_id=request.client_order_id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except VenueError as exc:
        raise HTTPException(502, str(exc)) from exc

    from forward.wiring.audit import get_audit_log_service

    get_audit_log_service().append(
        actor=principal.username,
        action="order.place",
        subject=f"{side} {abs(request.quantity)} {request.symbol}",
        detail=f"{request.order_type} @ {request.price or 'MKT'} -> {placed.status}",
    )
    return {
        "order_id": placed.order_id,
        "broker_order_id": placed.broker_order_id,
        "status": placed.status,
        "reject_reason": placed.reject_reason,
        # True when this was a repeat of an order already placed: nothing new was sent.
        "duplicate": placed.duplicate,
    }
