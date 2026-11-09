"""Quote-driven simulated broker used only by forward paper runs."""

from __future__ import annotations

from datetime import datetime

from forward.brokers.base import Broker
from forward.brokers.iifl.auth import IST
from forward.core.costs import CommissionModel
from forward.core.enums import OrderStatus, Side
from forward.core.instrument_models import Instrument
from forward.core.order_models import Fill, Order
from forward.core.portfolio_models import Funds, Holding, Position


class QuotePaperBroker(Broker):
    """Simulate immediate fills at current displayed bid or ask prices.

    Orders never reach a broker API. Missing bid/ask data rejects the simulated
    order; the last traded price is not substituted for an executable quote.
    """

    def __init__(
        self,
        quote_getter,
        *,
        commission: CommissionModel,
        max_quote_age_seconds: float,
    ) -> None:
        if max_quote_age_seconds <= 0:
            raise ValueError("max_quote_age_seconds must be positive")
        self.quote_getter = quote_getter
        self.commission = commission
        self.max_quote_age_seconds = max_quote_age_seconds
        self._orders: list[Order] = []
        self._fills: list[Fill] = []

    def place_order(self, order: Order) -> Order:
        """Fill against the current executable side of the displayed quote."""
        quote = self.quote_getter(order.instrument.symbol)
        price = (
            getattr(quote, "ask", None) if order.side is Side.BUY else getattr(quote, "bid", None)
        )
        size = (
            getattr(quote, "ask_size", None)
            if order.side is Side.BUY
            else getattr(quote, "bid_size", None)
        )
        if quote is None or price is None or float(price) <= 0:
            order.mark(OrderStatus.REJECTED, "current executable quote is unavailable")
            self._orders.append(order)
            return order
        if size is None or float(size) < order.quantity:
            order.mark(
                OrderStatus.REJECTED, "displayed quote size is missing or below requested quantity"
            )
            self._orders.append(order)
            return order
        timestamp = getattr(quote, "ts", None)
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if not isinstance(timestamp, datetime):
            order.mark(OrderStatus.REJECTED, "quote timestamp is unavailable")
            self._orders.append(order)
            return order
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=IST)
        quote_age = (datetime.now(IST) - timestamp.astimezone(IST)).total_seconds()
        if quote_age < -5 or quote_age > self.max_quote_age_seconds:
            order.mark(OrderStatus.REJECTED, "executable quote is stale or from the future")
            self._orders.append(order)
            return order
        fill_price = float(price)
        fee = self.commission.compute(order.quantity, fill_price, order.instrument, side=order.side)
        fill = Fill(
            order_id=order.order_id,
            instrument=order.instrument,
            side=order.side,
            quantity=order.quantity,
            price=fill_price,
            ts=timestamp,
            commission=fee,
            liquidity="TAKER",
        )
        order.apply_fill(order.quantity, fill_price)
        self._orders.append(order)
        self._fills.append(fill)
        return order

    def modify_order(self, broker_order_id: str, **fields) -> Order:
        """Refuse changes because quote-paper orders fill immediately."""
        raise NotImplementedError("quote-paper orders are filled or rejected immediately")

    def cancel_order(self, broker_order_id: str) -> bool:
        """Return false because the paper broker holds no resting orders."""
        return False

    def positions(self) -> list[Position]:
        """Return no broker-side positions; the runner owns paper accounting."""
        return []

    def open_orders(self) -> list[Order]:
        """Return the currently open order set, which is empty for immediate fills."""
        return [order for order in self._orders if order.is_active]

    def last_price(self, instruments: list[Instrument]) -> dict[str, float]:
        """Read last prices from the same live quote stream used for fills."""
        prices: dict[str, float] = {}
        for instrument in instruments:
            quote = self.quote_getter(instrument.symbol)
            last = getattr(quote, "last", None) if quote is not None else None
            if last is not None and float(last) > 0:
                prices[instrument.symbol] = float(last)
        return prices

    def fills_from_trades(self) -> list[Fill]:
        """Expose simulated fills to the shared live-runner portfolio fold."""
        return list(self._fills)

    def holdings(self) -> list[Holding]:
        """Return no settled holdings in an options paper simulation."""
        return []

    def funds(self) -> Funds:
        """Signal that funding is simulated, not read from the broker account."""
        raise NotImplementedError("quote-paper funds are tracked in the runner portfolio")
