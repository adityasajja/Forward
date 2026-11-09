"""Portfolio book records and shared portfolio calculations."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from forward.core.enums import Side


@dataclass
class BookPosition:
    """One symbol in one deployment: signed quantity, average cost, last mark."""

    symbol: str
    qty: float = 0.0
    avg_price: float = 0.0
    last_price: float = 0.0
    deployment_id: str | None = None
    strategy_id: str | None = None

    @property
    def value(self) -> float:
        price = self.last_price or self.avg_price or 0.0
        return self.qty * price


@dataclass
class DeploymentSlice:
    deployment_id: str
    strategy_id: str | None
    strategy_version: int | None
    mode: str | None
    status: str | None
    capital: float
    positions: dict[str, BookPosition] = field(default_factory=dict)
    #: Cash left after folding every fill against the allocation. Starts at
    #: ``capital`` — the number somebody chose — never at an invented default.
    cash: float | None = 0.0


@dataclass
class PortfolioBook:
    """Every deployment's lots, plus the capital they were allocated."""

    deployments: list[DeploymentSlice] = field(default_factory=list)
    total_capital: float = 0.0
    symbols_without_price: list[str] = field(default_factory=list)
    commission_complete: bool = True

    def arm_qty(self, deployment_id: str | None, symbol: str) -> float:
        if not deployment_id:
            return 0.0
        for dep in self.deployments:
            if dep.deployment_id == deployment_id:
                position = dep.positions.get(_clean(symbol))
                return position.qty if position else 0.0
        return 0.0

    def net_qty(self, symbol: str) -> float:
        wanted = _clean(symbol)
        return sum(
            position.qty
            for dep in self.deployments
            for position in dep.positions.values()
            if position.symbol == wanted
        )

    def symbol_value(self, symbol: str) -> float:
        wanted = _clean(symbol)
        return sum(
            position.value
            for dep in self.deployments
            for position in dep.positions.values()
            if position.symbol == wanted
        )

    def gross_exposure(self) -> float:
        return sum(abs(p.value) for dep in self.deployments for p in dep.positions.values())

    def open_symbols(self) -> list[str]:
        seen: dict[str, float] = {}
        for dep in self.deployments:
            for position in dep.positions.values():
                seen[position.symbol] = seen.get(position.symbol, 0.0) + position.qty
        return sorted(sym for sym, qty in seen.items() if abs(qty) > 1e-9)


def _clean(symbol: Any) -> str:
    return str(symbol or "").strip().upper()


def _side_sign(side: Any) -> int:
    """Return the sign of a canonical order side.

    Args:
        side: A ``BUY`` or ``SELL`` side.

    Returns:
        ``1`` for a buy and ``-1`` for a sell.

    Raises:
        ValueError: If the side is missing or is not canonical.
    """
    return Side.parse(side).sign


def _apply_fill(position: BookPosition, fill: dict[str, Any]) -> tuple[float, float]:
    """Fold one incremental fill into a lot, average-cost.

    Buys add cost; sells reduce quantity at the running average (realised P&L
    is the journal's job, not the book's). A sell beyond the held quantity
    opens a short at the fill price — average-cost across the zero line would
    fabricate a basis, so the flip restarts it.

    Returns:
        The validated fill quantity and price.

    Raises:
        ValueError: If quantity or price is missing, invalid, or not positive.
    """
    try:
        qty = float(fill["qty"])
        price = float(fill["price"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("portfolio fill must contain numeric qty and price") from error
    if not math.isfinite(qty) or qty <= 0:
        raise ValueError(f"portfolio fill quantity must be finite and positive, got {qty!r}")
    if not math.isfinite(price) or price <= 0:
        raise ValueError(f"portfolio fill price must be finite and positive, got {price!r}")

    signed = qty if _side_sign(fill["side"]) > 0 else -qty
    position.last_price = price
    new_qty = position.qty + signed
    if position.qty == 0 or (position.qty > 0) == (new_qty > 0) or new_qty == 0:
        if signed > 0:
            cost = position.avg_price * position.qty + price * signed
            position.avg_price = cost / new_qty if new_qty != 0 else 0.0
        position.qty = new_qty
        if new_qty == 0:
            position.avg_price = 0.0
    else:
        # Flipped through zero: the far side opens at this fill's price.
        position.qty = new_qty
        position.avg_price = price
    return qty, price


def sector_resolver(table: Mapping[str, str]) -> Any:
    """Symbol → sector, from the same universe mapping the dataset uses.

    The learning builder's ``sector`` column comes from this table, so
    concentration figures here agree with the learning axes by construction.
    Unknown symbols report ``"Unknown"`` — a real bucket in every breakdown,
    never a silent drop.
    """

    def _sector(symbol: Any) -> str:
        return table.get(_clean(symbol), "Unknown")

    return _sector
