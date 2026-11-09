"""Order domain models."""

from __future__ import annotations

import math
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from forward.core.enums import OrderStatus, OrderType, Side, TimeInForce
from forward.core.instrument_models import Instrument


class Order(BaseModel):
    model_config = ConfigDict(validate_assignment=False)

    order_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    instrument: Instrument
    side: Side
    quantity: float  # always positive; direction lives in `side`
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    stop_price: float | None = None
    tif: TimeInForce = TimeInForce.DAY
    status: OrderStatus = OrderStatus.PENDING
    parent_signal_id: str | None = None
    tag: str | None = None
    created_at: datetime | None = None
    filled_quantity: float = 0.0
    avg_fill_price: float = 0.0
    broker_order_id: str | None = None
    reject_reason: str | None = None

    #: Broker-specific extras (e.g. IIFL ``product`` / ``orderComplexity``).
    #: Keeping them here stops broker vocabulary leaking into the core model.
    broker_params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("quantity")
    @classmethod
    def _positive_qty(cls, v: float) -> float:
        if not math.isfinite(v) or v <= 0:
            raise ValueError("order quantity must be finite and positive")
        return v

    @field_validator("filled_quantity", "avg_fill_price")
    @classmethod
    def _nonnegative_finite_fill_state(cls, value: float) -> float:
        """Require valid cumulative fill state on a new order.

        Args:
            value: Filled quantity or average fill price.

        Returns:
            The validated value.

        Raises:
            ValueError: If the value is negative or not finite.
        """
        if not math.isfinite(value) or value < 0:
            raise ValueError("order fill state must be finite and non-negative")
        return value

    @field_validator("limit_price", "stop_price")
    @classmethod
    def _positive_finite_order_price(cls, value: float | None) -> float | None:
        """Require valid limit and stop prices when supplied.

        Args:
            value: Optional submitted order price.

        Returns:
            The validated price or ``None``.

        Raises:
            ValueError: If a supplied price is not finite and positive.
        """
        if value is not None and (not math.isfinite(value) or value <= 0):
            raise ValueError("order price must be finite and positive")
        return value

    @property
    def signed_quantity(self) -> float:
        return self.side.sign * self.quantity

    @property
    def remaining_quantity(self) -> float:
        return self.quantity - self.filled_quantity

    @property
    def is_active(self) -> bool:
        return self.status in (
            OrderStatus.PENDING,
            OrderStatus.SUBMITTED,
            OrderStatus.PARTIALLY_FILLED,
        )

    def mark(self, status: OrderStatus, reason: str | None = None) -> None:
        self.status = status
        if reason:
            self.reject_reason = reason

    def apply_fill(self, quantity: float, price: float) -> None:
        if not math.isfinite(quantity) or quantity <= 0:
            raise ValueError("fill quantity must be finite and positive")
        if not math.isfinite(price) or price <= 0:
            raise ValueError("fill price must be finite and positive")
        if quantity > self.remaining_quantity + 1e-9:
            raise ValueError("fill exceeds remaining quantity")
        total = self.filled_quantity + quantity
        self.avg_fill_price = (
            (self.avg_fill_price * self.filled_quantity) + (price * quantity)
        ) / total
        self.filled_quantity = total
        if abs(self.remaining_quantity) < 1e-9:
            self.status = OrderStatus.FILLED
        else:
            self.status = OrderStatus.PARTIALLY_FILLED


class Fill(BaseModel):
    fill_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    order_id: str
    instrument: Instrument
    side: Side
    quantity: float
    price: float
    ts: datetime
    #: ``None`` means the broker has not reported the fee; known zero is ``0.0``.
    commission: float | None = None
    slippage: float = 0.0
    liquidity: str = "TAKER"  # TAKER | MAKER
    broker_fill_id: str | None = None

    @field_validator("quantity", "price")
    @classmethod
    def _positive_finite_fill_value(cls, value: float) -> float:
        """Require positive finite fill quantities and prices.

        Args:
            value: Fill quantity or execution price.

        Returns:
            The validated value.

        Raises:
            ValueError: If the value is not finite and positive.
        """
        if not math.isfinite(value) or value <= 0:
            raise ValueError("fill value must be finite and positive")
        return value

    @field_validator("commission", "slippage")
    @classmethod
    def _finite_fill_cost(cls, value: float | None) -> float | None:
        """Require finite reported costs while allowing negative slippage.

        Args:
            value: Optional commission or signed slippage.

        Returns:
            The validated cost value.

        Raises:
            ValueError: If a supplied cost is not finite.
        """
        if value is not None and not math.isfinite(value):
            raise ValueError("fill cost must be finite")
        return value

    @property
    def gross_notional(self) -> float:
        return self.quantity * self.price * self.instrument.multiplier

    @property
    def signed_cash_flow(self) -> float | None:
        """Return cash impact when commission is known.

        A BUY removes cash from the account (negative), a SELL adds it. Unknown
        commission makes the final cash impact unknown too.
        """
        if self.commission is None:
            return None
        return -self.side.sign * self.gross_notional - self.commission
