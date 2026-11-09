"""Broker adapter for the execution venue port."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from forward.brokers.base import Broker
from forward.core.enums import OrderStatus, OrderType, Side
from forward.core.instrument_models import Instrument
from forward.core.order_models import Order
from forward.core.order_ports import OrderInstrumentLookupPort
from forward.execution.oms import OrderDraft
from forward.execution.venue import (
    VENUE_ACCEPTED,
    VENUE_FILLED,
    VENUE_PARTIAL,
    VENUE_REJECTED,
    VenueOutcome,
)


def outcome_from_broker_order(placed: Order) -> VenueOutcome:
    """Map a broker order report to the execution venue result.

    Args:
        placed: Order report returned by a broker.

    Returns:
        The venue result used by the order lifecycle.
    """
    status = placed.status
    if status is OrderStatus.REJECTED:
        reason = (placed.reject_reason or "").strip()
        if not reason:
            raise ValueError("broker rejected order without a reason")
        return VenueOutcome(
            status=VENUE_REJECTED,
            broker_order_id=placed.broker_order_id,
            reject_reason=reason,
            raw={"status": status.value, "reason": placed.reject_reason},
        )
    if status in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED):
        quantity = float(placed.filled_quantity)
        price = float(placed.avg_fill_price)
        if quantity <= 0 or price <= 0:
            raise ValueError(
                f"broker reported {status.value} without a positive filled quantity and price"
            )
        return VenueOutcome(
            status=(
                VENUE_FILLED
                if status is OrderStatus.FILLED
                else VENUE_PARTIAL
            ),
            broker_order_id=placed.broker_order_id,
            filled_qty=quantity,
            filled_price=price,
            raw={"status": status.value},
        )
    if status in (OrderStatus.PENDING, OrderStatus.SUBMITTED):
        return VenueOutcome(
            status=VENUE_ACCEPTED,
            broker_order_id=placed.broker_order_id,
            raw={"status": status.value},
        )
    raise ValueError(f"broker returned unexpected order status {status.value!r}")


@dataclass
class BrokerVenue:
    """A :class:`Venue` over the existing :class:`~forward.brokers.base.Broker` ABC.

    The broker is used unchanged. The only work here is building the core
    ``Order`` from a draft — and doing it in one place is the entire reason this
    class exists rather than each route constructing its own.
    """

    broker: Broker
    instruments: OrderInstrumentLookupPort

    def submit(self, draft: OrderDraft) -> VenueOutcome:
        """Convert an order draft and send it to the broker.

        Args:
            draft: Validated order intent.

        Returns:
            The broker report mapped to a venue result.
        """
        order_type = OrderType.parse(draft.order_type)
        instrument = self._instrument(draft)
        params: dict[str, Any] = {}
        if draft.product:
            params["product"] = draft.product
        side = Side.parse(draft.side)

        order = Order(
            instrument=instrument,
            side=side,
            quantity=abs(float(draft.quantity)),
            order_type=order_type,
            limit_price=draft.limit_price,
            # The trigger lives here, not in `broker_params`: `IiflBroker` reads
            # `order.stop_price` and nothing else.
            stop_price=draft.stop_price,
            tag=draft.tag,
            broker_params=params,
        )
        return outcome_from_broker_order(self.broker.place_order(order))

    def _instrument(self, draft: OrderDraft) -> Instrument:
        """Resolve the exact contract needed for a broker order.

        Args:
            draft: Order intent with the symbol and exchange.

        Returns:
            The listed instrument supplied by the lookup or broker master.

        """
        return self.instruments(draft.symbol, draft.exchange)


def iifl_venue(
    broker: Broker, instruments: OrderInstrumentLookupPort
) -> BrokerVenue:
    """Build a live venue over an authenticated IIFL broker.

    Args:
        broker: Authenticated broker adapter.
        instruments: Application port for exact listed-contract lookup.

    Returns:
        The venue that submits orders through that broker.
    """
    return BrokerVenue(broker=broker, instruments=instruments)
