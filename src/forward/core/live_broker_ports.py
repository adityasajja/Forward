"""Ports for live broker orders."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol


class LiveBrokerPort(Protocol):
    """Place and manage orders through a live broker."""

    def place_order(self, order: Any) -> Any:
        """Submit an order to the broker.

        Args:
            order: Domain order to submit.

        Returns:
            The broker's order report.
        """

    def positions(self) -> Sequence[Any]:
        """Return the broker's current positions."""

    def open_orders(self) -> Sequence[Any]:
        """Return the broker's current open orders."""

    def cancel_order(self, broker_order_id: str) -> bool:
        """Request cancellation of one broker order.

        Args:
            broker_order_id: ID returned when the broker accepted the order.

        Returns:
            Whether the broker accepted the cancellation request.
        """

    def cancel_all(self) -> int:
        """Cancel all open orders and return the number of requests sent."""

    def fills_from_trades(self) -> list[Any]:
        """Return fills not yet folded into the local portfolio."""


class LiveBrokerProviderPort(Protocol):
    """Create a broker connection for live deployment execution."""

    def __call__(self) -> LiveBrokerPort:
        """Return an authenticated broker adapter."""
