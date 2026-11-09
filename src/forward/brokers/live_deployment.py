"""Live venue for saved deployments; execution reports remain broker facts.

The scheduler shares DeploymentLoop's rules and session clocks with paper trading.
Only this adapter transmits and reconciles broker orders. A cancellation request
is never treated as confirmation, and missing order reports pause evaluation.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from forward.brokers.venue import BrokerVenue
from forward.core.enums import OrderStatus
from forward.core.order_ports import OrderInstrumentLookupPort
from forward.core.time import utcnow
from forward.execution.venue import VENUE_REJECTED, VenueOutcome


@dataclass
class LiveDeploymentVenue:
    broker: Any
    instruments: OrderInstrumentLookupPort
    prices: Callable
    max_tick_age_seconds: float
    clock: Callable = utcnow
    may_transmit: Callable[[], bool] = lambda: False

    def submit(self, draft):
        if not self.may_transmit():
            return VenueOutcome(
                status=VENUE_REJECTED, reject_reason="Live execution mode is not enabled"
            )
        return BrokerVenue(self.broker, self.instruments).submit(draft)

    def sync(self, loop, now: datetime) -> list[dict]:
        orders = [
            o
            for o in loop.orders.open_orders(loop.user_id)
            if o.get("deployment_id") == loop.deployment_id and o.get("mode") == "LIVE"
        ]
        if not orders:
            return []
        reports = {
            str(o.broker_order_id): o for o in self.broker.open_orders() if o.broker_order_id
        }
        updates = []
        for order in orders:
            broker_id = order.get("broker_order_id")
            if not broker_id or str(broker_id) not in reports:
                raise RuntimeError(
                    f"Broker outcome unknown for {order['order_id']}; reconciliation required"
                )
            report = reports[str(broker_id)]
            if report.instrument.symbol != order["symbol"] or report.side.name != order["side"]:
                raise RuntimeError("Broker report identity does not match the saved order")
            order_id = order["order_id"]
            if order["status"] == "SUBMITTED":
                loop.orders.acknowledge(order_id, loop.user_id, broker_order_id=str(broker_id))
            cumulative = float(report.filled_quantity)
            if cumulative > float(order.get("filled_quantity") or 0):
                if report.avg_fill_price <= 0:
                    raise RuntimeError("Broker fill report has no usable price")
                previous_qty = float(order.get("filled_quantity") or 0)
                previous_average = 0.0
                for event in loop.orders.history(order_id, loop.user_id):
                    if (
                        event.get("filled_qty") is not None
                        and float(event["filled_qty"]) == previous_qty
                    ):
                        previous_average = float(event.get("filled_price") or 0)
                incremental_price = (
                    float(report.avg_fill_price) * cumulative - previous_average * previous_qty
                ) / (cumulative - previous_qty)
                if incremental_price <= 0:
                    raise RuntimeError("Broker cumulative fill value went backwards")
                loop.orders.record_fill(
                    order_id,
                    loop.user_id,
                    filled_qty=cumulative,
                    filled_price=float(report.avg_fill_price),
                    raw={
                        "broker_order_id": str(broker_id),
                        "incremental_fill_price": incremental_price,
                    },
                )
                updates.append({"order_id": order_id, "filled_qty": cumulative})
            current = loop.orders.get(order_id, loop.user_id)
            if current["status"] == "FILLED":
                continue
            if report.status == OrderStatus.CANCELLED:
                if current["status"] != "CANCEL_PENDING":
                    loop.orders.request_cancel(order_id, loop.user_id, reason="broker cancelled")
                loop.orders.confirm_cancel(
                    order_id, loop.user_id, raw={"broker_order_id": str(broker_id)}
                )
                continue
            if report.status == OrderStatus.REJECTED:
                # ACKNOWLEDGED cannot be rewritten as REJECTED in the OMS. This
                # inconsistency must be reconciled rather than inventing a fill.
                raise RuntimeError(
                    f"Broker rejected working order {order_id}; reconciliation required"
                )
            if loop.config.entry_closed(now) and order["side"] == "BUY":
                self.cancel_entry(loop, current, "entry cutoff reached")
        return updates

    def cancel_entry(self, loop, order: dict, reason: str) -> None:
        if order["status"] != "CANCEL_PENDING":
            loop.orders.request_cancel(order["order_id"], loop.user_id, reason=reason)
        # A response only acknowledges the request. A later CANCELLED report
        # confirms it, including partial-fill races. Pending requests are retried.
        self.broker.cancel_order(str(order["broker_order_id"]))
