"""Portfolio domain models."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from forward.core.instrument_models import Instrument
from forward.core.order_models import Fill


class Position(BaseModel):
    instrument: Instrument
    quantity: float = 0.0
    avg_price: float = 0.0
    realized_pnl: float = 0.0
    last_price: float = 0.0
    opened_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def is_flat(self) -> bool:
        return abs(self.quantity) < 1e-9

    @property
    def direction(self) -> int:
        return 0 if self.is_flat else (1 if self.quantity > 0 else -1)

    @property
    def market_value(self) -> float:
        return self.quantity * self.last_price * self.instrument.multiplier

    @property
    def unrealized_pnl(self) -> float:
        if self.is_flat:
            return 0.0
        return (self.last_price - self.avg_price) * self.quantity * self.instrument.multiplier

    @property
    def exposure(self) -> float:
        return abs(self.market_value)

    def apply_fill(self, fill: Fill) -> float:
        """Fold a fill into the position, return realised P&L from this fill."""
        signed_new = fill.side.sign * fill.quantity
        realised = 0.0

        if self.is_flat:
            self.avg_price = fill.price
            self.quantity = signed_new
            self.opened_at = fill.ts
        elif self.direction == fill.side.sign:
            # Adding to the position: weighted average cost.
            total_qty = abs(self.quantity) + fill.quantity
            self.avg_price = (
                (self.avg_price * abs(self.quantity)) + (fill.price * fill.quantity)
            ) / total_qty
            self.quantity = self.direction * total_qty
        else:
            # Reducing or flipping.
            close_qty = min(abs(self.quantity), fill.quantity)
            realised = (
                (fill.price - self.avg_price)
                * close_qty
                * self.direction
                * self.instrument.multiplier
            )
            remaining = fill.quantity - close_qty
            new_qty = abs(self.quantity) - close_qty
            if remaining <= 1e-9:
                self.quantity = self.direction * new_qty
                if self.is_flat:
                    self.avg_price = 0.0
                    self.opened_at = None
            else:
                # Position flipped through to the other side: the old trade
                # is closed and a new one opens at this fill's timestamp.
                self.quantity = fill.side.sign * remaining
                self.avg_price = fill.price
                self.opened_at = fill.ts

        self.realized_pnl += realised
        self.updated_at = fill.ts
        return realised

    def mark(self, price: float, ts: datetime | None = None) -> None:
        """Re-price the position. A missing price is not a price.

        The feeds write NaN for a symbol with no bar on a given step (and the
        live path can hand over a None). Assigning it would make
        ``market_value`` — and therefore ``portfolio.equity`` — NaN for that
        bar, and the damage does not stay local: the equity curve records the
        NaN, every metric computed from it is NaN, and any strategy sizing off
        ``ctx.equity`` dies later as ``int(nan)`` somewhere unrelated.

        Keeping the last known price is also what ``buy_and_hold_equity``
        already does, and the benchmark and the portfolio have to treat a
        missing bar the same way or the comparison is between two conventions.
        """
        if price is None:
            return
        try:
            value = float(price)
        except (TypeError, ValueError):
            return
        if not math.isfinite(value) or value <= 0:
            return
        self.last_price = value
        if ts:
            self.updated_at = ts


class Holding(BaseModel):
    """A settled holding, as the broker reports it.

    Distinct from :class:`Position` on purpose. A *position* is what you are
    currently exposed to; a *holding* is what has settled into your demat account.
    On a T+1 market they legitimately differ for a day after every trade, which is
    exactly why reconciliation treats a position mismatch as critical and a holding
    mismatch as a warning.
    """

    instrument: Instrument
    quantity: float = 0.0
    avg_price: float = 0.0
    last_price: float = 0.0
    #: What can be sold today. Less than ``quantity`` while a purchase settles.
    sellable_quantity: float | None = None
    isin: str | None = None
    #: Anything the broker returned that we do not model, kept for post-mortems.
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def market_value(self) -> float:
        return self.quantity * self.last_price * self.instrument.multiplier


class Funds(BaseModel):
    """The account's cash position, as the broker reports it.

    ``available_cash`` is the only field the platform can use, and it is the one
    ``max_daily_loss`` needs: that limit is measured from equity, and equity is
    cash plus market value. Without this the limit cannot fire on the live path,
    which is why ``forward.brokers.portfolio.BrokerPortfolio.equity`` reports ``0.0`` today rather than a
    guess.
    """

    available_cash: float = 0.0
    margin_used: float = 0.0
    #: Total or withdrawable balance when the broker distinguishes them.
    withdrawable: float | None = None
    collateral: float | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def net(self) -> float:
        """Cash plus collateral: what the account is actually worth in cash terms."""
        return self.available_cash + (self.collateral or 0.0)
