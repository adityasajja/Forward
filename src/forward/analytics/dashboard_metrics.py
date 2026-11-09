"""Pure calculations for the main dashboard metrics."""

from __future__ import annotations

import contextlib
from datetime import UTC, date, datetime
from typing import Any

from forward.core.market_models import DashboardHistorySample

_PNL_KEYS = ("realized_pnl", "realised_pnl", "pnl", "net_pnl", "profit")
_TRADE_TS_KEYS = ("ts", "time", "trade_time", "fill_time", "order_time", "date")
BREADTH_SAMPLE_SIZE = 600
BREADTH_SESSIONS = 5
BREADTH_MINIMUM_BARS = 61


def trade_day(trade: dict[str, Any]) -> str | None:
    """Return the calendar date for one broker trade timestamp.

    Args:
        trade: Normalized broker trade record.

    Returns:
        The trade date in ISO format, or ``None`` when no timestamp is usable.
    """
    raw = next((trade[key] for key in _TRADE_TS_KEYS if trade.get(key)), None)
    if raw is None:
        return None
    text = str(raw)
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    with contextlib.suppress(TypeError, ValueError, OSError):
        timestamp = float(text)
        if timestamp > 1e11:
            timestamp /= 1000.0
        return datetime.fromtimestamp(timestamp, tz=UTC).strftime("%Y-%m-%d")
    return None


def trade_pnl(trade: dict[str, Any]) -> float | None:
    """Return the first populated broker P&L value as a number.

    Args:
        trade: Normalized broker trade record.

    Returns:
        The first populated P&L value, or ``None`` when it is missing or invalid.
    """
    value = next((trade[key] for key in _PNL_KEYS if trade.get(key) is not None), None)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def performance_from_trades(
    trades: list[dict[str, Any]], *, today: date
) -> dict[str, Any]:
    """Calculate realized P&L, win rate, drawdown, and daily returns.

    Args:
        trades: Broker trades in newest-first order.
        today: Exchange-local date used to calculate today's realized P&L.

    Returns:
        The dashboard performance summary with the existing response fields.
    """
    realized: list[float] = []
    by_day: dict[str, float] = {}
    for trade in trades:
        pnl = trade_pnl(trade)
        if pnl is None:
            continue
        realized.append(pnl)
        day = trade_day(trade)
        if day is not None:
            by_day[day] = by_day.get(day, 0.0) + pnl

    window = realized[:30]
    wins = sum(1 for pnl in window if pnl > 0)
    losses = sum(1 for pnl in window if pnl < 0)
    equity = peak = max_drawdown = 0.0
    curve = []
    for pnl in reversed(realized):
        equity += pnl
        peak = max(peak, equity)
        drawdown = (equity - peak) / abs(peak) * 100.0 if peak else 0.0
        max_drawdown = min(max_drawdown, drawdown)
        curve.append(round(equity, 2))
    current_drawdown = (equity - peak) / abs(peak) * 100.0 if peak else 0.0

    recent = []
    if by_day:
        running_total = 0.0
        for day in sorted(by_day)[-7:]:
            running_total += by_day[day]
            recent.append(round(running_total, 2))

    today_text = today.isoformat()
    realized_today = round(by_day.get(today_text, 0.0), 2)
    return {
        "trades": len(realized),
        "win_rate": round(wins / len(window) * 100.0, 1) if window else None,
        "wins": wins,
        "losses": losses,
        "sample": len(window),
        "realized_pnl": round(sum(realized), 2),
        "realized_pnl_today": realized_today,
        "current_drawdown_pct": round(current_drawdown, 2),
        "max_drawdown_pct": round(max_drawdown, 2),
        "sparkline": recent,
        "equity_curve": curve[-40:],
    }


def calculate_breadth(history: DashboardHistorySample) -> dict[str, Any]:
    """Calculate five-session breadth from sampled daily closes.

    Args:
        history: Cached daily closes and the full market-universe size.

    Returns:
        The breadth series, dates, latest value, and sample counts.
    """
    flags: dict[int, list[bool]] = {back: [] for back in range(BREADTH_SESSIONS)}
    last_dates: dict[int, str | None] = {back: None for back in range(BREADTH_SESSIONS)}
    for closes in history.symbols.values():
        if len(closes) < BREADTH_MINIMUM_BARS:
            continue
        values = [close.close for close in closes]
        count = len(values)
        for back in range(BREADTH_SESSIONS):
            if count <= back + 60:
                continue
            index = count - 1 - back
            average_20 = _sma_at(values, index, 20)
            average_50 = _sma_at(values, index, 50)
            flags[back].append(values[index] > average_20 > average_50)
            if last_dates[back] is None:
                last_dates[back] = closes[index].session

    series: list[float] = []
    dates: list[str] = []
    for back in range(BREADTH_SESSIONS - 1, -1, -1):
        total = len(flags[back])
        if total:
            series.append(round(sum(flags[back]) / total * 100.0, 1))
            dates.append(last_dates[back] or "")

    delta = series[-1] - series[0] if len(series) >= 2 else None
    return {
        "series": series,
        "dates": dates,
        "current": series[-1] if series else None,
        "delta_5d": round(delta, 2) if delta is not None else None,
        "expanding": None if delta is None else delta > 0,
        "sampled": min(len(history.symbols), BREADTH_SAMPLE_SIZE),
        "universe": history.universe_size,
    }


def _sma_at(values: list[float], index: int, window: int) -> float:
    """Calculate the rolling average ending at one close index.

    Args:
        values: Daily close values.
        index: Last value included in the average.
        window: Number of values to include.

    Returns:
        The average, or NaN when the requested window starts before the data.
    """
    start = index - window + 1
    if start < 0:
        return float("nan")
    return sum(values[start : index + 1]) / window
