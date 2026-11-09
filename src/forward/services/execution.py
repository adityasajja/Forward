"""Execution service: the one path from an intent to a venue.

Why this module exists
----------------------
Before it, three code paths built broker orders by hand — the manual order route,
the signal-execute route, and the live runner — and each of them got to decide
what an order was. Two of the three were wrong in ways that only show up at the
exchange:

* ``api/main.py:662`` passed ``OrderType.SL_MARKET``, which **does not exist** on
  the enum. The signal-execute route therefore placed its *entry* order and then
  raised ``AttributeError`` on the stop-loss leg, returning a 500 and leaving a
  live position with no stop and no target.
* Even had the name existed, the leg set ``limit_price`` and a
  ``broker_params["triggerPrice"]`` that nothing reads. ``IiflBroker`` takes the
  trigger from ``order.stop_price``, so the stop would have gone to the exchange
  with no trigger price.

Both are the same failure: an order assembled at the call site, where nothing
checks it. So orders are assembled here, once, and the venue is a parameter.

The venue abstraction is also what makes ``BACKTEST → PAPER → LIVE`` real: the
same :class:`~forward.services.orders.OrderService` lifecycle and the same rule layer,
with a different :class:`~forward.execution.venue.Venue`. A backtest fills from bars, paper fills from the
live quote with slippage, live transmits. Three venues, one strategy definition.

What this service refuses to do
-------------------------------
**It will not re-submit a duplicate.** If ``open_order`` reports that the intent
already has an order, this returns that order untouched. Re-submitting on a retry
is exactly the double-order the idempotency guard exists to prevent, and a guard
that is bypassed by the retry path guards nothing.

**It will not invent an outcome when the venue call fails.** If the broker call
raises, the order is left at ``SUBMITTED`` — which is the truth: the request was
sent and we do not know whether it landed. Writing a rejection there would be a
lie, and writing a fill would be a worse one. Reconciliation resolves unknowns;
this service records them as unknown.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from forward.core.errors import VenueError as _VenueError
from forward.execution.oms import OrderDraft, manual_idempotency_key
from forward.execution.venue import (
    VENUE_REJECTED as _VENUE_REJECTED,
)
from forward.execution.venue import (
    Venue as _Venue,
)
from forward.execution.venue import (
    VenueOutcome as _VenueOutcome,
)
from forward.services.orders import OrderService

logger = logging.getLogger("forward.services.execution")


@dataclass(frozen=True)
class PlacementResult:
    """The outcome of one :meth:`ExecutionService.place` call."""

    order_id: str
    status: str
    created: bool
    order: dict[str, Any]
    broker_order_id: str | None = None
    filled_quantity: float = 0.0
    reject_reason: str | None = None
    duplicate_of: str | None = None

    @property
    def duplicate(self) -> bool:
        return not self.created


@dataclass
class ExecutionService:
    """Drives one intent through the OMS and out to a venue."""

    orders: OrderService
    venue: _Venue

    def place_manual(
        self,
        *,
        user_id: str,
        symbol: str,
        signed_quantity: int,
        exchange: str,
        order_type: str,
        price: float | None,
        product: str | None,
        tag: str | None,
        client_order_id: str | None,
    ) -> PlacementResult:
        """Build and submit one manually requested live order.

        Args:
            user_id: Account that owns the order.
            symbol: Trading symbol.
            signed_quantity: Positive for a buy and negative for a sell.
            exchange: Exchange segment for the symbol.
            order_type: Broker order type.
            price: Limit price when the order is not market-priced.
            product: Broker product code.
            tag: User label stored with the order.
            client_order_id: Client-generated ID used to retry one submission.

        Returns:
            The result from the shared order lifecycle.
        """
        side = "BUY" if signed_quantity > 0 else "SELL"
        draft = OrderDraft(
            user_id=user_id,
            symbol=symbol,
            side=side,
            quantity=abs(signed_quantity),
            mode="LIVE",
            exchange=exchange,
            order_type=order_type,
            limit_price=price,
            product=product,
            requested_price=price,
            tag=tag,
        )
        idempotency_key = (
            manual_idempotency_key(user_id=user_id, client_order_id=client_order_id)
            if client_order_id
            else None
        )
        return self.place(draft, idempotency_key=idempotency_key)

    def place(
        self,
        draft: OrderDraft,
        *,
        idempotency_key: str | None = None,
        request_id: str | None = None,
    ) -> PlacementResult:
        """Create, risk-check, submit, transmit — in that order, once.

        The order of operations is the safety property. Risk is evaluated before
        anything is transmitted, and the ``SUBMITTED`` event is written before the
        venue call so that a crash mid-transmission leaves a record that the
        request went out rather than no record at all.
        """
        opened = self.orders.open_order_intent(
            draft, idempotency_key=idempotency_key, request_id=request_id
        )
        order_id = opened.order_id

        if not opened.created:
            existing = self._require_order(order_id, draft.user_id)
            logger.info(
                "duplicate intent for %s %s: returning order %s at %s without resubmitting",
                draft.side,
                draft.symbol,
                order_id,
                existing.get("status"),
            )
            return self._result(order_id, existing, created=False, duplicate_of=opened.duplicate_of)

        status = self.orders.validate(order_id, draft.user_id)
        if status != "RISK_APPROVED":
            logger.warning("order %s refused by risk", order_id)
            return self._result(
                order_id, self._require_order(order_id, draft.user_id), created=True
            )

        self.orders.submit(order_id, draft.user_id, request_id=request_id)

        try:
            outcome = self.venue.submit(draft)
        except Exception as exc:  # noqa: BLE001 - the order stays SUBMITTED, deliberately
            logger.exception("venue submission failed for order %s", order_id)
            raise _VenueError(
                f"the venue call failed for order {order_id}; the order is SUBMITTED "
                f"and its state at the exchange is unknown ({exc})",
                order_id=order_id,
            ) from exc

        self._apply(order_id, draft.user_id, outcome, request_id=request_id)
        return self._result(
            order_id, self._require_order(order_id, draft.user_id), created=True
        )

    def _require_order(self, order_id: str, user_id: str) -> dict[str, Any]:
        """Load an order row that the OMS has already created.

        Args:
            order_id: Order identifier returned by the OMS.
            user_id: Account that owns the order.

        Returns:
            The persisted order row.

        Raises:
            RuntimeError: If the OMS cannot read back the order it created.
        """
        order = self.orders.get(order_id, user_id)
        if order is None:
            raise RuntimeError(f"order {order_id!r} was not found after creation")
        return order

    def _apply(
        self,
        order_id: str,
        user_id: str,
        outcome: _VenueOutcome,
        *,
        request_id: str | None,
    ) -> None:
        """Fold the venue's answer into the lifecycle.

        A fill is recorded *after* the acknowledgement, because that is the order
        the exchange reports them in and because ``ACKNOWLEDGED`` is what makes a
        fill a legal transition from a state we actually passed through.
        """
        if outcome.status == _VENUE_REJECTED:
            if not outcome.reject_reason:
                raise ValueError("a rejected venue outcome must include a reason")
            self.orders.reject(
                order_id,
                user_id,
                reason=outcome.reject_reason,
                source="broker",
                raw=outcome.raw,
                request_id=request_id,
            )
            return

        self.orders.acknowledge(
            order_id,
            user_id,
            broker_order_id=outcome.broker_order_id,
            raw=outcome.raw,
            request_id=request_id,
        )
        if outcome.filled and outcome.filled_qty > 0:
            self.orders.record_fill(
                order_id,
                user_id,
                filled_qty=outcome.filled_qty,
                filled_price=draft_reference_price(outcome),
                commission=outcome.commission,
                raw=outcome.raw,
                request_id=request_id,
            )

    def _result(
        self,
        order_id: str,
        order: dict[str, Any],
        *,
        created: bool,
        duplicate_of: str | None = None,
    ) -> PlacementResult:
        return PlacementResult(
            order_id=order_id,
            status=str(order["status"]),
            created=created,
            broker_order_id=order.get("broker_order_id"),
            filled_quantity=float(order["filled_quantity"]),
            reject_reason=order.get("reject_reason"),
            duplicate_of=duplicate_of,
            order=order,
        )


def draft_reference_price(outcome: _VenueOutcome) -> float:
    """A fill price we can defend, or a refusal.

    A fill with no price cannot be recorded: ``orders.avg_fill_price`` would be
    zero and every P&L figure downstream would be wrong in a way nobody could
    trace back to here. Better to fail loudly than to write a zero.
    """
    if outcome.filled_price:
        return float(outcome.filled_price)
    raise ValueError(
        "a filled venue outcome must carry a price — refusing to record a fill at zero"
    )


__all__ = [
    "ExecutionService",
    "PlacementResult",
]
