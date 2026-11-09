"""Broker-agnostic execution interface.

The backtest engine never imports a concrete broker; live trading swaps
:class:`SimulatedBroker` for an implementation of :class:`Broker`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from forward.core.instrument_models import Instrument
from forward.core.order_models import Order
from forward.core.portfolio_models import Funds, Holding, Position


class BrokerError(RuntimeError):
    pass


class Broker(ABC):
    @abstractmethod
    def place_order(self, order: Order) -> Order:
        """Submit an order. Returns the order with ``broker_order_id`` set."""

    @abstractmethod
    def modify_order(self, broker_order_id: str, **fields) -> Order: ...

    @abstractmethod
    def cancel_order(self, broker_order_id: str) -> bool: ...

    @abstractmethod
    def positions(self) -> list[Position]: ...

    @abstractmethod
    def open_orders(self) -> list[Order]: ...

    @abstractmethod
    def last_price(self, instruments: list[Instrument]) -> dict[str, float]: ...

    # ---------------------------------------------------------------- reconciliation
    @abstractmethod
    def holdings(self) -> list[Holding]:
        """Return settled holdings in the demat account.

        Returns:
            The broker's settled holdings.
        """

    @abstractmethod
    def funds(self) -> Funds:
        """Return available account funds and margin.

        Returns:
            The broker's current funds report.
        """

    def cancel_all(self) -> int:
        cancelled = 0
        for order in self.open_orders():
            if order.broker_order_id:
                self.cancel_order(order.broker_order_id)
                cancelled += 1
        return cancelled
