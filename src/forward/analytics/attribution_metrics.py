"""Return and risk measurements for trade attribution."""

from __future__ import annotations

import math

from forward.core.enums import Side


def gross_return_pct(
    entry_price: float | None, exit_price: float | None, side: str
) -> float | None:
    """Calculate the price return from entry to exit.

    Args:
        entry_price: Price paid or received when the position opened.
        exit_price: Price received or paid when the position closed.
        side: Position direction, either buy or sell.

    Returns:
        Signed price return in percent, or ``None`` when a price is missing.

    Raises:
        ValueError: If ``side`` is not the canonical ``BUY`` or ``SELL`` value.
    """
    if not entry_price or exit_price is None:
        return None
    sign = float(Side.parse(side).sign)
    return (float(exit_price) / float(entry_price) - 1.0) * 100.0 * sign


def net_return_pct(
    net_pnl: float | None,
    position_value: float | None,
) -> float | None:
    """Calculate net return from recorded money values.

    Args:
        net_pnl: Recorded profit or loss after costs.
        position_value: Recorded value of the position at entry.

    Returns:
        Net return in percent, or ``None`` when net P&L or position value is
        missing or unusable.
    """
    if net_pnl is None or position_value is None:
        return None
    net_value = float(net_pnl)
    invested_value = float(position_value)
    if not math.isfinite(net_value) or not math.isfinite(invested_value) or invested_value <= 0:
        return None
    return net_value / invested_value * 100.0


def realized_risk_pct(
    *,
    entry_price: float | None,
    stop_price: float | None,
    mae_pct: float | None,
) -> float | None:
    """Measure position risk from its stop or recorded adverse move.

    Args:
        entry_price: Price at which the position opened.
        stop_price: Recorded protective stop price.
        mae_pct: Maximum adverse excursion in percent when no stop was recorded.

    Returns:
        Risk as a percent of entry, or ``None`` when it cannot be measured.
    """
    if entry_price and stop_price:
        return abs(float(entry_price) - float(stop_price)) / float(entry_price) * 100.0
    if mae_pct is not None:
        return abs(float(mae_pct))
    return None
