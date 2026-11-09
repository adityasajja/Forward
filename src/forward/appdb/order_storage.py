"""SQL adapter for order transactions and event history."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import datetime
from typing import Any

from forward.appdb.engine import AppDatabase
from forward.appdb.order_event_repository import OrderEventRepository
from forward.appdb.order_intent_repository import OrderIntentRepository
from forward.appdb.order_repository import OrderRepository
from forward.core.order_ports import (
    OrderMonitoringStoragePort,
    OrderStoragePort,
    OrderUnitOfWorkPort,
)


class SqlOrderStorage(OrderStoragePort, OrderMonitoringStoragePort):
    """Open database transactions for order operations."""

    def __init__(self, database: AppDatabase) -> None:
        """Create the adapter for one application database.

        Args:
            database: Database that owns the SQL session lifecycle.
        """
        self._database = database

    @contextmanager
    def transaction(self) -> Iterator[OrderUnitOfWorkPort]:
        """Open one transactional order unit of work.

        Yields:
            A unit of work bound to the active database session.
        """
        with self._database.session() as session:
            yield SqlOrderUnitOfWork(session)

    def monitoring_order_log(
        self,
        user_id: str,
        deployment_id: str,
        *,
        order_limit: int,
        event_limit: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Read a deployment's order rows and event rows.

        Args:
            user_id: Account that owns the deployment.
            deployment_id: Deployment whose order history is requested.
            order_limit: Maximum order rows to read.
            event_limit: Maximum event rows to read.

        Returns:
            Order rows and their matching event rows.
        """
        with self._database.session() as session:
            orders, _total = OrderRepository.list_for_user(
                session, user_id, deployment_id=deployment_id, limit=order_limit
            )
            events = OrderEventRepository.for_user(
                session,
                user_id,
                deployment_id=deployment_id,
                limit=event_limit,
            )
        return orders, events


class SqlOrderUnitOfWork(OrderUnitOfWorkPort):
    """Apply order repository operations inside one SQL session."""

    def __init__(self, session: Any) -> None:
        """Create a unit of work bound to an active session.

        Args:
            session: Session managed by ``SqlOrderStorage``.
        """
        self._session = session

    def savepoint(self) -> AbstractContextManager[None]:
        """Open a nested transaction for one provisional write.

        Returns:
            A context manager that rolls back its writes when an error escapes.
        """
        return self._session.begin_nested()

    def get_order(self, order_id: str, user_id: str) -> dict[str, Any] | None:
        """Read an order owned by the account.

        Args:
            order_id: Order to read.
            user_id: Account that must own the order.

        Returns:
            The order row, or ``None`` when it does not belong to the account.
        """
        return OrderRepository.get(self._session, order_id, user_id)

    def list_orders(
        self,
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
        """Return one owner-scoped order page and its total count.

        Args:
            user_id: Account whose orders are requested.
            status: Optional status filter.
            symbol: Optional symbol filter.
            deployment_id: Optional deployment filter.
            correlation_id: Optional correlation filter.
            mode: Optional execution mode filter.
            since: Optional lower bound for order creation time.
            limit: Maximum number of rows in the page.
            offset: Number of rows to skip.

        Returns:
            The order rows and matching row count.
        """
        return OrderRepository.list_for_user(
            self._session,
            user_id,
            status=status,
            symbol=symbol,
            deployment_id=deployment_id,
            correlation_id=correlation_id,
            mode=mode,
            since=since,
            limit=limit,
            offset=offset,
        )

    def open_orders(
        self, user_id: str, *, deployment_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Return non-terminal orders owned by the account.

        Args:
            user_id: Account whose orders are requested.
            deployment_id: Optional deployment filter.

        Returns:
            The account's open order rows.
        """
        return OrderRepository.open_orders(
            self._session, user_id, deployment_id=deployment_id
        )

    def order_history(self, order_id: str) -> list[dict[str, Any]]:
        """Return the order's events from oldest to newest.

        Args:
            order_id: Order whose event history is requested.

        Returns:
            Events in sequence order.
        """
        return OrderEventRepository.history(self._session, order_id)

    def fill_history(self, order_id: str) -> list[dict[str, Any]]:
        """Return execution events for the order.

        Args:
            order_id: Order whose fills are requested.

        Returns:
            Fill events in sequence order.
        """
        return OrderEventRepository.fills(self._session, order_id)

    def get_intent(self, idempotency_key: str) -> dict[str, Any] | None:
        """Return the order intent that owns an idempotency key.

        Args:
            idempotency_key: Stable key used to suppress duplicate orders.

        Returns:
            The stored intent, or ``None`` when the key is unused.
        """
        return OrderIntentRepository.get(self._session, idempotency_key)

    def create_order(self, values: dict[str, Any]) -> dict[str, Any]:
        """Insert an order and its initial event.

        Args:
            values: Order fields accepted by the order store.

        Returns:
            The inserted order row.
        """
        return OrderRepository.create(self._session, **values)

    def claim_intent(self, values: dict[str, Any]) -> tuple[str, bool]:
        """Claim an idempotency key and return its order ID and insert result.

        Args:
            values: Idempotency fields accepted by the intent store.

        Returns:
            The owning order ID and whether this call created the claim.
        """
        return OrderIntentRepository.create_if_absent(self._session, **values)

    def latest_event(self, order_id: str) -> dict[str, Any] | None:
        """Return the latest event for the order.

        Args:
            order_id: Order whose latest event is requested.

        Returns:
            The latest event, or ``None`` when no event exists.
        """
        return OrderEventRepository.latest(self._session, order_id)

    def append_event(self, values: dict[str, Any]) -> dict[str, Any]:
        """Append an event and update the order projection.

        Args:
            values: Event fields accepted by the order event store.

        Returns:
            The appended event row.
        """
        return OrderEventRepository.append(self._session, **values)
