"""SQL repository for the append-only order event log."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from forward.appdb.engine import utcnow
from forward.appdb.execution_schema import order_events, orders
from forward.appdb.repository_support import json_text, many, new_id, one

#: Statuses from which no further transition is legal. Kept here rather than in
#: the OMS because two writers need it: the OMS to refuse the transition, and
#: these repositories to set `completed_at` and to stop accepting progress.
TERMINAL_ORDER_STATUSES = frozenset({"FILLED", "CANCELLED", "REJECTED", "EXPIRED"})


class OrderEventRepository:
    """The append-only order log. There is no update and no delete here."""

    @staticmethod
    def _next_seq(session: Session, order_id: str) -> int:
        stmt = select(func.max(order_events.c.seq)).where(
            order_events.c.order_id == order_id
        )
        return int(session.execute(stmt).scalar() or 0) + 1

    @staticmethod
    def append(
        session: Session,
        *,
        order_id: str,
        to_status: str,
        from_status: str | None = None,
        ts: datetime | None = None,
        broker_ts: datetime | None = None,
        ack_ts: datetime | None = None,
        fill_ts: datetime | None = None,
        requested_price: float | None = None,
        filled_price: float | None = None,
        filled_qty: float | None = None,
        slippage_bps: float | None = None,
        commission: float | None = None,
        latency_ms: int | None = None,
        reject_reason: str | None = None,
        raw: dict[str, Any] | str | None = None,
        source: str = "oms",
        correlation_id: str | None = None,
        request_id: str | None = None,
        broker_order_id: str | None = None,
    ) -> dict[str, Any]:
        """Append one transition and reproject ``orders`` from it.

        ``filled_qty`` is **cumulative**, not incremental — the same convention
        the broker uses, so the parsed field and the raw payload cannot disagree.
        A single fill's size is the difference between consecutive events, which
        ``seq`` makes unambiguous.

        Fields the event does not carry are left out of the projection rather
        than written as ``NULL``: an ``ACKNOWLEDGED`` event must not erase the
        ``requested_price`` recorded when the order was raised, or every slippage
        figure downstream becomes unmeasurable the moment an order is acked.
        """
        to_status = (to_status or "").strip().upper()
        if not to_status:
            raise ValueError("to_status is required")
        ts = ts or utcnow()
        event: dict[str, Any] = {
            "order_event_id": new_id(),
            "order_id": order_id,
            "seq": 0,
            "from_status": from_status,
            "to_status": to_status,
            "ts": ts,
            "broker_ts": broker_ts,
            "ack_ts": ack_ts,
            "fill_ts": fill_ts,
            "requested_price": requested_price,
            "filled_price": filled_price,
            "filled_qty": filled_qty,
            "slippage_bps": slippage_bps,
            "commission": commission,
            "latency_ms": latency_ms,
            "reject_reason": (reject_reason or None) and str(reject_reason)[:255],
            "raw": json_text(raw),
            "source": (source or "oms")[:16],
            "correlation_id": correlation_id,
            "request_id": request_id,
        }
        # The retry is for the sequence allocator losing a race, which is the
        # only way `UNIQUE(order_id, seq)` can fire on a well-formed event.
        for attempt in range(2):
            event["seq"] = OrderEventRepository._next_seq(session, order_id)
            try:
                with session.begin_nested():
                    session.execute(insert(order_events).values(**event))
                break
            except IntegrityError:
                if attempt == 1:
                    raise
                continue
        OrderEventRepository._project(
            session, order_id, event, broker_order_id=broker_order_id
        )
        return event

    @staticmethod
    def _project(
        session: Session,
        order_id: str,
        event: dict[str, Any],
        *,
        broker_order_id: str | None = None,
    ) -> None:
        """Fold one event into ``orders``, in the caller's transaction.

        Written here, next to the insert, rather than by the OMS: two writes that
        must agree should not be two decisions made in two places. The invariant
        "``orders.status`` equals the newest event's ``to_status``" is asserted
        directly by ``tests/test_oms.py``.
        """
        values: dict[str, Any] = {
            "status": event["to_status"],
            "updated_at": event["ts"],
        }
        if event.get("filled_qty") is not None:
            values["filled_quantity"] = float(event["filled_qty"])
        if event.get("filled_price") is not None:
            values["avg_fill_price"] = float(event["filled_price"])
        if event.get("requested_price") is not None:
            values["requested_price"] = float(event["requested_price"])
        if event.get("reject_reason"):
            values["reject_reason"] = event["reject_reason"]
        if broker_order_id:
            values["broker_order_id"] = broker_order_id
        if event["to_status"] == "SUBMITTED":
            values["submitted_at"] = event["ts"]
        if event["to_status"] in TERMINAL_ORDER_STATUSES:
            values["completed_at"] = event["ts"]
        session.execute(
            update(orders).where(orders.c.order_id == order_id).values(**values)
        )

    @staticmethod
    def history(session: Session, order_id: str) -> list[dict[str, Any]]:
        """Every transition, oldest first. ``seq`` is the ordering authority."""
        stmt = (
            select(order_events)
            .where(order_events.c.order_id == order_id)
            .order_by(order_events.c.seq)
        )
        return many(session.execute(stmt).all())

    @staticmethod
    def latest(session: Session, order_id: str) -> dict[str, Any] | None:
        stmt = (
            select(order_events)
            .where(order_events.c.order_id == order_id)
            .order_by(order_events.c.seq.desc())
            .limit(1)
        )
        return one(session.execute(stmt).first())

    @staticmethod
    def current_status(session: Session, order_id: str) -> str | None:
        """Status folded from the log alone — the check that the projection is honest."""
        stmt = select(order_events.c.to_status).where(
            order_events.c.order_id == order_id
        ).order_by(order_events.c.seq.desc()).limit(1)
        value = session.execute(stmt).scalar()
        return None if value is None else str(value)

    @staticmethod
    def fills_for_orders(
        session: Session, order_ids: list[str], *, include_state_changes: bool = False
    ) -> dict[str, list[dict[str, Any]]]:
        """Read execution events in bounded batches instead of one query per order."""
        grouped: dict[str, list[dict[str, Any]]] = {}
        for start in range(0, len(order_ids), 500):
            stmt = (
                select(order_events)
                .where(
                    order_events.c.order_id.in_(order_ids[start:start + 500]),
                    order_events.c.filled_qty.is_not(None),
                )
                .order_by(order_events.c.order_id, order_events.c.seq)
            )
            if not include_state_changes:
                stmt = stmt.where(order_events.c.to_status.in_(("PARTIALLY_FILLED", "FILLED")))
            for row in many(session.execute(stmt).all()):
                grouped.setdefault(row["order_id"], []).append(row)
        return grouped

    @staticmethod
    def fills(session: Session, order_id: str) -> list[dict[str, Any]]:
        """Events that represent execution, not just a state change."""
        stmt = (
            select(order_events)
            .where(
                order_events.c.order_id == order_id,
                order_events.c.to_status.in_(("PARTIALLY_FILLED", "FILLED")),
                order_events.c.filled_qty.is_not(None),
            )
            .order_by(order_events.c.seq)
        )
        return many(session.execute(stmt).all())

    @staticmethod
    def for_user(
        session: Session,
        user_id: str,
        *,
        since: datetime | None = None,
        limit: int = 500,
        deployment_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Every event on every one of a user's orders, newest last.

        Used by reconciliation and by the trade journal, both of which need the
        whole chain rather than one order at a time. ``deployment_id`` scopes
        to one deployment: without it a user with many deployments silently
        loses this deployment's older events outside the newest-first window.
        """
        stmt = (
            select(order_events)
            .join(orders, orders.c.order_id == order_events.c.order_id)
            .where(orders.c.user_id == user_id)
        )
        if deployment_id:
            stmt = stmt.where(orders.c.deployment_id == deployment_id)
        if since:
            stmt = stmt.where(order_events.c.ts >= since)
        stmt = stmt.order_by(order_events.c.ts.desc()).limit(max(1, min(limit, 5000)))
        return many(session.execute(stmt).all())
