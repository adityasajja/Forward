"""SQL repository for durable order idempotency."""

from __future__ import annotations

from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from forward.appdb.engine import utcnow
from forward.appdb.execution_schema import order_intents
from forward.appdb.repository_support import one


class IdempotencyConflict(RuntimeError):
    """An idempotency key exists but belongs to a different caller.

    This should be unreachable — the key is derived from fields that are already
    user-scoped — and it is raised rather than swallowed because the alternative
    is returning *another user's order* to this caller, which is the one failure
    mode a duplicate-order guard must never have.
    """


# The key's *derivation* lives in ``forward.execution.oms`` alongside ``OrderDraft``,
# because it is a statement about intent rather than about storage and the route
# layer needs it without being allowed to import this module. Only the storage of
# the key belongs here, where its primary key is.


class OrderIntentRepository:
    """Durable idempotency. The primary key *is* the mechanism."""

    @staticmethod
    def get(session: Session, idempotency_key: str) -> dict[str, Any] | None:
        """Return the intent that owns an idempotency key.

        Args:
            session: Active transaction session.
            idempotency_key: Stable key reserved for one order intent.

        Returns:
            The owning intent, or ``None`` when the key is unused.
        """
        stmt = select(order_intents).where(
            order_intents.c.idempotency_key == idempotency_key
        )
        return one(session.execute(stmt).first())

    @staticmethod
    def create_if_absent(
        session: Session,
        *,
        idempotency_key: str,
        order_id: str,
        user_id: str,
        strategy_id: str | None = None,
        strategy_version: int | None = None,
        signal_id: str | None = None,
    ) -> tuple[str, bool]:
        """Claim ``idempotency_key`` for ``order_id``.

        Returns ``(order_id, created)``. On a duplicate it returns the order that
        already owns the key and ``created=False`` — it does **not** raise,
        because a retry is the normal case this exists for, not an error.

        The insert runs in a savepoint so a duplicate does not break the outer
        transaction. After an integrity error, only a row with this exact key
        counts as a retry. Other database errors keep their original meaning.
        """
        now = utcnow()
        statement = insert(order_intents).values(
            idempotency_key=idempotency_key,
            order_id=order_id,
            user_id=user_id,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            signal_id=signal_id,
            created_at=now,
        )
        try:
            with session.begin_nested():
                session.execute(statement)
        except IntegrityError as exc:
            existing = OrderIntentRepository.get(session, idempotency_key)
            if existing is None:
                raise
            if existing["user_id"] != user_id:
                raise IdempotencyConflict(
                    f"idempotency key {idempotency_key[:12]}… belongs to another account"
                ) from exc
            return str(existing["order_id"]), False
        return order_id, True

    @staticmethod
    def for_order(session: Session, order_id: str) -> dict[str, Any] | None:
        """Return the intent recorded for an order.

        Args:
            session: Active transaction session.
            order_id: Order whose intent is requested.

        Returns:
            The intent, or ``None`` when no intent exists.
        """
        stmt = select(order_intents).where(order_intents.c.order_id == order_id)
        return one(session.execute(stmt).first())
