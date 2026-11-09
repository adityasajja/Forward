"""Encode the message frames sent by the live market WebSocket."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import orjson


@dataclass
class LiveTickPayload:
    """Normalized market tick fields sent to browser clients."""

    symbol: str
    exchange: str
    ltp: float
    last_qty: int
    volume: int
    open: float
    high: float
    low: float
    close: float
    chg: float
    chg_pct: float
    best_bid: float | None
    best_ask: float | None
    ts: str
    epoch: float = 0.0
    depth: list[dict[str, Any]] | None = None


def encode_tick_frame(tick: LiveTickPayload) -> str:
    """Encode one tick with the existing WebSocket frame shape.

    Args:
        tick: Normalized market tick to send.

    Returns:
        UTF-8 JSON text containing a ``tick`` frame.
    """
    return orjson.dumps({"type": "tick", "data": asdict(tick)}).decode("utf-8")


def encode_status_frame(bridge_connected: bool, server_time: str) -> str:
    """Encode initial connection status for a WebSocket client.

    Args:
        bridge_connected: Whether the live market bridge is connected.
        server_time: Server timestamp already formatted by the caller.

    Returns:
        UTF-8 JSON text containing a ``status`` frame.
    """
    return orjson.dumps(
        {
            "type": "status",
            "bridge_connected": bridge_connected,
            "server_time": server_time,
        }
    ).decode("utf-8")
