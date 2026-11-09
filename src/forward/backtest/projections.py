"""Pure projections for backtest results returned to callers."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def timestamp_text(value: Any) -> str | None:
    """Format a stored timestamp with the API's UTC suffix.

    Args:
        value: Timestamp value from a backtest record.

    Returns:
        ISO timestamp text with a trailing Z, or None when no value exists.
    """
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return f"{value.isoformat()}Z"
    return str(value)


def curve_point(row: dict[str, Any]) -> dict[str, Any]:
    """Project one stored curve row into the public point shape.

    Args:
        row: Stored curve point.

    Returns:
        Timestamp and value fields for an API response.
    """
    return {"ts": timestamp_text(row.get("ts")), "value": row.get("value")}


def trade_result(row: dict[str, Any]) -> dict[str, Any]:
    """Project one stored trade into its public response shape.

    Args:
        row: Stored backtest trade record.

    Returns:
        Stable trade fields for the API response.
    """
    return {
        "seq": row.get("seq"),
        "symbol": row.get("symbol"),
        "direction": row.get("direction"),
        "quantity": row.get("quantity"),
        "entry_ts": timestamp_text(row.get("entry_ts")),
        "entry_price": row.get("entry_price"),
        "exit_ts": timestamp_text(row.get("exit_ts")),
        "exit_price": row.get("exit_price"),
        "gross_pnl": row.get("gross_pnl"),
        "commission": row.get("commission"),
        "net_pnl": row.get("net_pnl"),
        "return_pct": row.get("return_pct"),
        "duration_days": row.get("duration_days"),
        "exit_reason": row.get("exit_reason"),
        "signal_reason": row.get("signal_reason"),
        "strategy_version": row.get("strategy_version"),
    }


def compound_returns(values: Iterable[float | None]) -> float | None:
    """Compound monthly percentage returns into one total return.

    Args:
        values: Monthly percentage returns. Missing months are skipped.

    Returns:
        Compounded percentage return, or None when no month has a return.
    """
    total = 1.0
    seen = False
    for value in values:
        if value is None:
            continue
        total *= 1.0 + (float(value) / 100.0)
        seen = True
    return (total - 1.0) * 100.0 if seen else None
