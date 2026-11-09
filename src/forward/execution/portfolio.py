"""Portfolio accounting — the single source of truth for money.

Deliberately modelled so that equity == cash + market value at all times. If
that identity ever breaks, there is a bug in the fill or margin logic, and the
engine asserts on it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from forward.core.enums import AssetClass
from forward.core.instrument_models import Instrument
from forward.core.order_models import Fill
from forward.core.portfolio_models import Position

EPS = 1e-6


@dataclass
class Portfolio:
    initial_cash: float = 1_000_000.0
    #: Fraction of notional posted for cash-equity margin.
    #: Derivatives use the per-unit margin on the Instrument instead.
    equity_margin_ratio: float = 1.0
    allow_short: bool = True

    cash: float = field(init=False)
    positions: dict[str, Position] = field(default_factory=dict, init=False)
    realized_pnl: float = field(default=0.0, init=False)
    commission_paid: float = field(default=0.0, init=False)
    commission_complete: bool = field(default=True, init=False)
    slippage_cost: float = field(default=0.0, init=False)
    _curve: list[tuple[datetime, float, float]] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        self.cash = self.initial_cash

    # ------------------------------------------------------------------
    def position(self, symbol: str) -> Position:
        return self.positions.get(symbol, Position(instrument=Instrument(symbol=symbol)))

    def ensure_position(self, instrument: Instrument) -> Position:
        pos = self.positions.get(instrument.symbol)
        if pos is None:
            pos = Position(instrument=instrument)
            self.positions[instrument.symbol] = pos
        return pos

    # ------------------------------------------------------------------
    def mark(self, ts: datetime, prices: dict[str, float]) -> None:
        for symbol, price in prices.items():
            pos = self.positions.get(symbol)
            if pos:
                pos.mark(price, ts)
        self._curve.append((ts, self.equity, self.cash))

    @property
    def market_value(self) -> float:
        return sum(p.market_value for p in self.positions.values())

    @property
    def unrealized_pnl(self) -> float:
        return sum(p.unrealized_pnl for p in self.positions.values())

    @property
    def equity(self) -> float:
        return self.cash + self.market_value

    @property
    def gross_exposure(self) -> float:
        return sum(p.exposure for p in self.positions.values())

    @property
    def net_exposure(self) -> float:
        return sum(p.market_value for p in self.positions.values())

    # ------------------------------------------------------------------
    def margin_required(self, position: Position) -> float | None:
        """Return required margin, or ``None`` when derivative margin is unknown.

        Cash equities use the configured notional ratio. Derivatives use only
        their supplied per-unit margin; the portfolio does not estimate it.
        Other asset classes have no margin rule in this portfolio.

        Args:
            position: Position whose margin is being measured.

        Returns:
            Required margin in account currency, or ``None`` when the position
            is a derivative without a positive supplied margin, or another
            asset class without a margin rule.
        """
        inst = position.instrument
        qty = abs(position.quantity)
        if qty < EPS:
            return 0.0
        if inst.is_derivative:
            if inst.initial_margin_per_unit <= 0:
                return None
            return qty * inst.initial_margin_per_unit
        if inst.asset_class is AssetClass.EQUITY:
            return abs(position.market_value) * self.equity_margin_ratio
        return None

    @property
    def margin_used(self) -> float | None:
        """Return total margin, or ``None`` when any open derivative is unknown."""
        requirements = [self.margin_required(position) for position in self.positions.values()]
        if any(requirement is None for requirement in requirements):
            return None
        return sum(requirement for requirement in requirements if requirement is not None)

    @property
    def buying_power(self) -> float | None:
        """Return equity left after margin, or ``None`` when margin is unknown."""
        margin_used = self.margin_used
        if margin_used is None:
            return None
        return self.equity - margin_used

    def margin_for_order(
        self, instrument: Instrument, quantity: float, price: float
    ) -> float | None:
        """Return the margin for an order, or ``None`` when F&O margin is unknown.

        Args:
            instrument: Instrument being traded.
            quantity: Order quantity.
            price: Order price, used for the cash-equity notional rule.

        Returns:
            Required margin, or ``None`` for a derivative without supplied
            positive per-unit margin or another asset class without a rule.
        """
        if abs(quantity) < EPS:
            return 0.0
        if instrument.is_derivative:
            if instrument.initial_margin_per_unit <= 0:
                return None
            return abs(quantity) * instrument.initial_margin_per_unit
        if instrument.asset_class is AssetClass.EQUITY:
            return abs(quantity * price * instrument.multiplier) * self.equity_margin_ratio
        return None

    # ------------------------------------------------------------------
    def apply_fill(self, fill: Fill) -> float:
        """Post a fill to cash and position; return realised P&L."""
        position = self.ensure_position(fill.instrument)
        realised = position.apply_fill(fill)

        cash_flow = fill.signed_cash_flow
        if cash_flow is None:
            self.commission_complete = False
            cash_flow = -fill.side.sign * fill.gross_notional
        else:
            self.commission_paid += fill.commission
        self.cash += cash_flow
        self.slippage_cost += abs(fill.slippage) * fill.quantity * fill.instrument.multiplier
        self.realized_pnl += realised

        if position.is_flat:
            position.last_price = 0.0
        return realised

    # ------------------------------------------------------------------
    def equity_curve(self) -> pd.Series:
        if not self._curve:
            return pd.Series(dtype=float)
        index = [c[0] for c in self._curve]
        values = [c[1] for c in self._curve]
        return pd.Series(values, index=pd.DatetimeIndex(index), name="equity")

    def cash_curve(self) -> pd.Series:
        if not self._curve:
            return pd.Series(dtype=float)
        index = [c[0] for c in self._curve]
        values = [c[2] for c in self._curve]
        return pd.Series(values, index=pd.DatetimeIndex(index), name="cash")

    def check_invariant(self) -> None:
        """equity must equal initial cash + realised + unrealised - commission.

        Slippage is already baked into fill prices, so it is not subtracted
        again here. A break here means the fill or margin path is wrong.
        """
        expected = self.initial_cash + self.realized_pnl + self.unrealized_pnl
        if self.commission_complete:
            expected -= self.commission_paid
        actual = self.equity
        if abs(expected - actual) > 1.0:
            raise AssertionError(
                f"portfolio invariant broken: expected {expected:.2f}, got {actual:.2f}"
            )
