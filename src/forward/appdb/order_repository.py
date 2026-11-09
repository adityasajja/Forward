"""SQL repository for user-owned orders."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session

from forward.appdb.engine import utcnow
from forward.appdb.execution_schema import orders
from forward.appdb.order_event_repository import (
    TERMINAL_ORDER_STATUSES,
    OrderEventRepository,
)
from forward.appdb.repository_support import many, new_id, one


class OrderRepository:
    """Orders. Status is written only by :meth:`OrderEventRepository.append`."""

    @staticmethod
    def create(
        session: Session,
        *,
        user_id: str,
        symbol: str,
        side: str,
        quantity: float,
        mode: str = "PAPER",
        exchange: str = "NSEEQ",
        asset_class: str = "EQUITY",
        order_type: str = "MARKET",
        limit_price: float | None = None,
        stop_price: float | None = None,
        tif: str = "DAY",
        product: str | None = None,
        requested_price: float | None = None,
        deployment_id: str | None = None,
        strategy_id: str | None = None,
        strategy_version: int | None = None,
        signal_id: str | None = None,
        correlation_id: str | None = None,
        tag: str | None = None,
        request_id: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Insert an order *and* its opening ``NEW`` event.

        The two writes are one call on purpose. An order row with no events is
        malformed — the log is what the lifecycle is reconstructed from — and a
        caller that had to remember to write the first event would eventually
        forget. Starting status is not a parameter for the same reason.

        ``reason`` is written to the ``NEW`` event's ``raw`` payload and *not* to
        the order row: the row is current state, and why an order was raised is
        history. ``tag`` is a 64-character marker and cannot hold a sentence, so
        the reason needs its own home to survive to the monitoring timeline.

        The ``raw`` payload also carries the **provenance stamp** — see
        :data:`forward.execution.oms.PROVENANCE_FORWARD`. This method is the only
        path that raises an order, so it is the only place the stamp can
        honestly be written, and writing it here means every order that goes
        through the platform is marked without its caller having to remember.
        A row inserted straight into the table — by a backfill harness, a
        migration, or a test — carries no stamp, and the learning engine grades
        it in-sample for exactly that reason.
        """
        from forward.execution.oms import (
            PROVENANCE_FORWARD,
            PROVENANCE_KEY,
            RECORDED_AT_KEY,
        )

        mode = (mode or "PAPER").strip().upper()
        if mode not in ("PAPER", "LIVE"):
            raise ValueError(f"mode must be PAPER or LIVE, got {mode!r}")
        side = (side or "").strip().upper()
        if side not in ("BUY", "SELL"):
            raise ValueError(f"side must be BUY or SELL, got {side!r}")
        if float(quantity) <= 0:
            raise ValueError("quantity must be positive")
        # Canonicalise on the way in, so `SL-M` and `STOP` are one stored value and
        # an unrecognised spelling is refused here rather than at a broker call.
        # This is the fix for `OrderType.SL_MARKET`, which did not exist: the
        # signal-execute route placed its entry leg and then raised on the stop.
        from forward.core.enums import OrderType

        order_type = OrderType.parse(order_type).value

        now = utcnow()
        order_id = new_id()
        row = {
            "order_id": order_id,
            "user_id": user_id,
            "deployment_id": deployment_id,
            "strategy_id": strategy_id,
            "strategy_version": strategy_version,
            "signal_id": signal_id,
            "correlation_id": correlation_id,
            "symbol": symbol.strip().upper(),
            "exchange": exchange,
            "asset_class": asset_class,
            "side": side,
            "quantity": float(quantity),
            "order_type": order_type,
            "limit_price": limit_price,
            "stop_price": stop_price,
            "tif": tif,
            "product": product,
            "mode": mode,
            "status": "NEW",
            "broker_order_id": None,
            "filled_quantity": 0.0,
            "avg_fill_price": 0.0,
            "requested_price": requested_price,
            "tag": tag,
            "reject_reason": None,
            "created_at": now,
            "updated_at": now,
            "submitted_at": None,
            "completed_at": None,
        }
        session.execute(insert(orders).values(**row))
        OrderEventRepository.append(
            session,
            order_id=order_id,
            from_status=None,
            to_status="NEW",
            ts=now,
            requested_price=requested_price,
            source="oms",
            correlation_id=correlation_id,
            request_id=request_id,
            raw={
                "reason": reason,
                PROVENANCE_KEY: PROVENANCE_FORWARD,
                RECORDED_AT_KEY: now.isoformat(),
            },
        )
        return OrderRepository.get(session, order_id, user_id) or row

    @staticmethod
    def get(session: Session, order_id: str, user_id: str) -> dict[str, Any] | None:
        stmt = select(orders).where(orders.c.order_id == order_id, orders.c.user_id == user_id)
        return one(session.execute(stmt).first())

    @staticmethod
    def get_by_broker_order_id(
        session: Session, broker_order_id: str, user_id: str
    ) -> dict[str, Any] | None:
        stmt = select(orders).where(
            orders.c.broker_order_id == broker_order_id, orders.c.user_id == user_id
        )
        return one(session.execute(stmt).first())

    @staticmethod
    def list_for_user(
        session: Session,
        user_id: str,
        *,
        status: str | None = None,
        symbol: str | None = None,
        deployment_id: str | None = None,
        correlation_id: str | None = None,
        mode: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        conditions = [orders.c.user_id == user_id]
        if status:
            conditions.append(orders.c.status == status)
        if symbol:
            conditions.append(orders.c.symbol == symbol.strip().upper())
        if deployment_id:
            conditions.append(orders.c.deployment_id == deployment_id)
        if correlation_id:
            conditions.append(orders.c.correlation_id == correlation_id)
        if mode:
            conditions.append(orders.c.mode == mode.strip().upper())
        if since:
            conditions.append(orders.c.created_at >= since)

        total = int(
            session.execute(select(func.count()).select_from(orders).where(*conditions)).scalar()
            or 0
        )
        stmt = (
            select(orders)
            .where(*conditions)
            .order_by(orders.c.created_at.desc())
            .limit(max(1, min(limit, 500)))
            .offset(max(0, offset))
        )
        return many(session.execute(stmt).all()), total

    @staticmethod
    def opening_for_strategy(
        session: Session, user_id: str, strategy_id: str, symbol: str
    ) -> list[dict[str, Any]]:
        """Read matching strategy orders from oldest to newest.

        Args:
            session: The caller's database transaction.
            user_id: Account that owns the orders.
            strategy_id: Strategy that raised the orders.
            symbol: Traded instrument symbol.

        Returns:
            Matching order rows in creation order.
        """
        statement = (
            select(orders)
            .where(
                orders.c.user_id == user_id,
                orders.c.strategy_id == strategy_id,
                orders.c.symbol == symbol.strip().upper(),
            )
            .order_by(orders.c.created_at)
        )
        return many(session.execute(statement).all())

    @staticmethod
    def for_replay(
        session: Session,
        user_id: str,
        *,
        deployment_id: str | None = None,
        mode: str | None = None,
    ) -> list[dict[str, Any]]:
        """All matching orders, without UI counts, offsets or page-size caps.

        Replay already retains the whole order mapping in memory. Read it once
        rather than repeatedly scanning/counting the same growing history.
        """
        conditions = [orders.c.user_id == user_id]
        if deployment_id:
            conditions.append(orders.c.deployment_id == deployment_id)
        if mode:
            conditions.append(orders.c.mode == mode.strip().upper())
        stmt = select(orders).where(*conditions).order_by(orders.c.created_at.desc())
        return many(session.execute(stmt).all())

    @staticmethod
    def open_orders(
        session: Session, user_id: str, *, deployment_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Orders that are neither filled nor cancelled nor rejected.

        Defined as "not in :data:`TERMINAL_ORDER_STATUSES`" rather than as a list
        of open statuses, so adding a status to the machine cannot silently leave
        orders out of reconciliation.
        """
        stmt = (
            select(orders)
            .where(
                orders.c.user_id == user_id,
                orders.c.status.notin_(sorted(TERMINAL_ORDER_STATUSES)),
            )
            .order_by(orders.c.created_at)
        )
        if deployment_id is not None:
            stmt = stmt.where(orders.c.deployment_id == deployment_id)
        return many(session.execute(stmt).all())
