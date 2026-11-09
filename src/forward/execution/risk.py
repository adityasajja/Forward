"""Pre-trade and intraday risk controls.

These run identically in backtest and live. The point is that a strategy bug
costs you a halted simulation, not a blown account.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, time

from forward.core.enums import Side
from forward.core.order_models import Order


@dataclass
class RiskLimits:
    max_gross_exposure: float | None = None
    max_position_notional: float | None = None
    max_position_per_symbol: float | None = None  # in units
    max_daily_loss: float | None = None
    max_daily_trades: int | None = None
    max_open_positions: int | None = None
    max_order_notional: float | None = None
    allow_short: bool = True
    allowed_symbols: set[str] | None = None
    trading_window: tuple[time, time] | None = None
    kill_switch: bool = False


@dataclass
class RiskVerdict:
    allowed: bool
    reason: str | None = None

    @classmethod
    def ok(cls) -> RiskVerdict:
        return cls(True)

    @classmethod
    def reject(cls, reason: str) -> RiskVerdict:
        return cls(False, reason)


#: Rejection reason when an order cannot be priced against finite notional
#: caps. A shared constant (not a string literal in two files) so the OMS
#: adapter can recognise this refusal and report its own code for it.
NO_REFERENCE_PRICE_REASON = "no reference price to enforce notional limits"


@dataclass
class RiskEngine:
    limits: RiskLimits = field(default_factory=RiskLimits)

    day_start_equity: float | None = field(default=None, init=False)
    orders_today: int = field(default=0, init=False)
    halted: bool = field(default=False, init=False)
    halt_reason: str | None = field(default=None, init=False)

    # ------------------------------------------------------------------
    def reset(self) -> None:
        self.day_start_equity = None
        self.orders_today = 0
        self.halted = False
        self.halt_reason = None

    def reset_daily(self, equity_at_rollover: float) -> None:
        """Start a new session with the equity recorded at rollover.

        ``max_daily_loss`` is measured from the start of the day, so the caller
        Leaving ``day_start_equity`` pinned across days is the other failure
        mode: the limit silently becomes "cumulative drawdown since inception",
        trips once, and reports a single-day loss that never happened.
        """
        if not math.isfinite(equity_at_rollover):
            raise ValueError("daily risk baseline must be finite")
        self.orders_today = 0
        self.day_start_equity = equity_at_rollover
        self.halted = False
        self.halt_reason = None

    def trip(self, reason: str) -> None:
        if not reason.strip():
            raise ValueError("risk halt needs a reason")
        self.halted = True
        self.halt_reason = reason

    def _required_halt_reason(self) -> str:
        """Return the reason for a halt or report an invalid engine state."""
        if self.halt_reason is None:
            raise RuntimeError("risk engine is halted without a reason")
        return self.halt_reason

    # ------------------------------------------------------------------
    def check(self, portfolio, now: datetime) -> RiskVerdict:
        """Portfolio-level check, run once per bar."""
        if self.limits.kill_switch:
            return RiskVerdict.reject("kill switch engaged")
        if self.halted:
            return RiskVerdict.reject(self._required_halt_reason())

        if self.limits.max_daily_loss is not None:
            if self.day_start_equity is None:
                raise RuntimeError("daily risk baseline is missing; call reset_daily first")
            loss = self.day_start_equity - portfolio.equity
            if loss >= self.limits.max_daily_loss:
                self.trip(f"daily loss {loss:,.2f} >= limit {self.limits.max_daily_loss:,.2f}")
                return RiskVerdict.reject(self.halt_reason)

        limit = self.limits.max_gross_exposure
        if limit is not None and portfolio.gross_exposure > limit:
            return RiskVerdict.reject(
                f"gross exposure {portfolio.gross_exposure:,.0f} > {limit:,.0f}"
            )

        if self.limits.max_open_positions is not None:
            open_count = sum(1 for p in portfolio.positions.values() if not p.is_flat)
            if open_count > self.limits.max_open_positions:
                return RiskVerdict.reject(f"too many open positions ({open_count})")

        if self.limits.trading_window is not None:
            start, end = self.limits.trading_window
            if not (start <= now.time() <= end):
                return RiskVerdict.reject(f"outside trading window {start}-{end}")

        return RiskVerdict.ok()

    # ------------------------------------------------------------------
    def is_risk_reducing(self, order: Order, portfolio) -> bool:
        """Return whether an order lowers a position's absolute size.

        A flip through zero to the opposite side counts when the resulting
        position is smaller than the original position.

        Args:
            order: Order being checked.
            portfolio: Current portfolio used to read the held quantity.

        Returns:
            Whether the order reduces the position's absolute quantity.
        """
        held = portfolio.position(order.instrument.symbol).quantity
        projected = held + order.signed_quantity
        return abs(projected) < abs(held) - 1e-9

    def check_order(self, order: Order, portfolio) -> RiskVerdict:
        """Per-order check, run before submission."""
        if self.limits.kill_switch:
            return RiskVerdict.reject("kill switch engaged")
        # A halt must still let the engine flatten the book, but it must never
        # let an order add risk. This lets ``ctx.close_all()`` work after a halt.
        if self.halted:
            if not self.is_risk_reducing(order, portfolio):
                return RiskVerdict.reject(self._required_halt_reason())
            return RiskVerdict.ok()

        symbol = order.instrument.symbol
        if self.limits.allowed_symbols is not None and symbol not in self.limits.allowed_symbols:
            return RiskVerdict.reject(f"{symbol} not in allowed universe")

        if order.side is Side.SELL and not self.limits.allow_short:
            held = portfolio.position(symbol).quantity
            if order.quantity > held + 1e-9:
                return RiskVerdict.reject(f"shorting disabled, holding {held}")

        price = order.limit_price
        if price is None:
            price = portfolio.position(symbol).last_price
        notional = (
            order.quantity * price * order.instrument.multiplier if price > 0 else None
        )
        order_cap = self.limits.max_order_notional
        position_cap = self.limits.max_position_notional
        order_cap_set = order_cap is not None
        position_cap_set = position_cap is not None
        if notional is None and (order_cap_set or position_cap_set):
            # No reference price (a market order on a never-marked symbol):
            # the notional is unknowable, and treating it as zero would wave
            # through any size. Reject rather than guess.
            return RiskVerdict.reject(NO_REFERENCE_PRICE_REASON)

        if order_cap_set and notional is not None and notional > order_cap:
            return RiskVerdict.reject(f"order notional {notional:,.0f} exceeds limit")

        if self.limits.max_position_per_symbol is not None:
            projected = abs(portfolio.position(symbol).quantity + order.signed_quantity)
            if projected > self.limits.max_position_per_symbol:
                return RiskVerdict.reject(
                    f"projected position {projected} > {self.limits.max_position_per_symbol}"
                )

        if position_cap_set:
            held = portfolio.position(symbol).quantity
            projected_notional = (
                abs(held + order.signed_quantity) * price * order.instrument.multiplier
            )
            if projected_notional > position_cap:
                return RiskVerdict.reject(
                    f"projected position notional {projected_notional:,.0f} exceeds limit"
                )

        if (
            self.limits.max_daily_trades is not None
            and self.orders_today >= self.limits.max_daily_trades
        ):
            return RiskVerdict.reject(f"daily trade limit reached ({self.orders_today})")

        self.orders_today += 1
        return RiskVerdict.ok()
