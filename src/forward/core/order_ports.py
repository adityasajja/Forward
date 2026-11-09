"""Ports for the related application workflows."""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime
from typing import Any, Protocol

from forward.core.instrument_models import Instrument


class OrderInstrumentLookupPort(Protocol):
    """Resolve an order's exact listed instrument."""

    def __call__(self, symbol: str, exchange: str) -> Instrument:
        """Return the contract or raise when it is not in the catalog.

        Args:
            symbol: Exact order symbol.
            exchange: Listing segment for the order.

        Returns:
            Validated contract details used by risk checks.
        """


class OrderMonitoringStoragePort(Protocol):
    """Read one deployment's orders and events for its timeline."""

    def monitoring_order_log(
        self, user_id: str, deployment_id: str, *, order_limit: int, event_limit: int
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Read the deployment's order rows and event rows.



        Args:

            user_id: Account that owns the deployment.

            deployment_id: Deployment whose order history is requested.

            order_limit: Maximum order rows to return.

            event_limit: Maximum event rows to return.



        Returns:

            Order rows and their events.

        """


class OrderUnitOfWorkPort(Protocol):
    """Read and write orders inside one storage transaction."""

    def savepoint(self) -> AbstractContextManager[None]:
        """Open a nested transaction for one provisional write.



        Returns:

            A context manager that rolls back its writes when an error escapes.

        """

    def get_order(self, order_id: str, user_id: str) -> dict[str, Any] | None:
        """Read an order owned by the account.



        Args:

            order_id: Order to read.

            user_id: Account that must own the order.



        Returns:

            The order row, or ``None`` when it does not belong to the account.

        """

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

    def order_history(self, order_id: str) -> list[dict[str, Any]]:
        """Return the order's events from oldest to newest.



        Args:

            order_id: Order whose event history is requested.



        Returns:

            Events in sequence order.

        """

    def fill_history(self, order_id: str) -> list[dict[str, Any]]:
        """Return execution events for the order.



        Args:

            order_id: Order whose fills are requested.



        Returns:

            Fill events in sequence order.

        """

    def get_intent(self, idempotency_key: str) -> dict[str, Any] | None:
        """Return the order intent that owns an idempotency key.



        Args:

            idempotency_key: Stable key used to suppress duplicate orders.



        Returns:

            The stored intent, or ``None`` when the key is unused.

        """

    def create_order(self, values: dict[str, Any]) -> dict[str, Any]:
        """Insert an order and its initial event.



        Args:

            values: Order fields accepted by the order store.



        Returns:

            The inserted order row.

        """

    def claim_intent(self, values: dict[str, Any]) -> tuple[str, bool]:
        """Claim an idempotency key and return its order ID and insert result.



        Args:

            values: Idempotency fields accepted by the intent store.



        Returns:

            The owning order ID and whether this call created the claim.

        """

    def latest_event(self, order_id: str) -> dict[str, Any] | None:
        """Return the latest event for the order.



        Args:

            order_id: Order whose latest event is requested.



        Returns:

            The latest event, or ``None`` when no event exists.

        """

    def append_event(self, values: dict[str, Any]) -> dict[str, Any]:
        """Append an event and update the order projection.



        Args:

            values: Event fields accepted by the order event store.



        Returns:

            The appended event row.

        """


class OrderStoragePort(Protocol):
    """Open transactions for the order use case."""

    def transaction(self) -> AbstractContextManager[OrderUnitOfWorkPort]:
        """Open one transaction and provide its order unit of work.



        Returns:

            A context manager that commits on success and rolls back on error.

        """
