"""Helpers shared by more than one route module."""

from __future__ import annotations

from typing import Any

import pandas as pd
from fastapi import HTTPException

from forward.core.backtest_ports import BacktestFeedPort
from forward.core.serialization import json_safe


def _authed_client():
    """IIFL client with a restored session. Works in any env — market data
    and other read APIs don't need paper/live mode (only order placement
    goes through the `_live_broker` gate below).

    Delegates to `forward.wiring.broker_access.get_authenticated_broker_client`, which is the one place
    that knows how to build and authenticate a client.
    """
    from forward.core.errors import BrokerUnavailable
    from forward.wiring.broker_access import get_authenticated_broker_client

    try:
        return get_authenticated_broker_client()
    except BrokerUnavailable as exc:
        raise HTTPException(exc.status, str(exc)) from exc


# ----------------------------------------------------------------------
# Serialisation helpers
# ----------------------------------------------------------------------
def _clean(value: Any) -> Any:
    """Make numpy/pandas output JSON-safe.

    ``NaN`` and ``inf`` are not valid JSON, and the metrics dataclasses emit
    both (profit factor with no losses is ``inf``, an untraded fold is ``nan``).
    Starlette would happily write the literal ``NaN`` token, which ``JSON.parse``
    rejects — so every payload crossing this boundary goes through here.
    """
    return json_safe(value)


def _equity_points(series: pd.Series | None, max_points: int = 900) -> list[dict[str, Any]]:
    """Downsample an equity curve for the wire without changing its shape."""
    if series is None or len(series) == 0:
        return []
    stride = max(1, len(series) // max_points)
    return [
        {"ts": pd.Timestamp(ts).isoformat(), "value": float(v)}
        for ts, v in zip(series.index[::stride], series.to_numpy()[::stride], strict=True)
    ]


def _history_feed(symbols: list[str], exchange: str) -> tuple[BacktestFeedPort, list[str]]:
    """Load selected cached daily bars for an API request.

    Args:
        symbols: Trading symbols requested by the caller.
        exchange: Exchange segment that stores the bars.

    Returns:
        The cached feed and the symbols included in it.

    Raises:
        HTTPException: If the cache is empty or has none of the requested symbols.
    """
    from forward.wiring.research import get_research_history_service

    wanted = [symbol.upper() for symbol in symbols]
    service = get_research_history_service()
    try:
        return service.load_cached_daily_feed(wanted, exchange)
    except FileNotFoundError as error:
        raise HTTPException(
            503,
            f"history cache is empty for {exchange} — run "
            f"`forward history sync --exchange {exchange}`",
        ) from error
    except LookupError as error:
        raise HTTPException(
            404,
            f"none of {wanted} are in the {exchange} cache — run "
            f"`forward history sync --exchange {exchange}`",
        ) from error


_EGRESS: dict[str, Any] = {"ip": None, "at": 0.0}


def _egress_ip(ttl: float = 600.0) -> str | None:
    """The public IPv4 this host egresses as, for diagnosing the IP whitelist.

    Fetched lazily and cached — it only runs once IIFL has already rejected us
    for an IP reason, so it costs nothing on the happy path.

    Uses a **dual-stack** endpoint with the same IPv4 pinning as `IiflClient`.
    An IPv4-only service would cheerfully report the IPv4 while the request
    itself left over IPv6, which is the exact confusion that hid this bug the
    first time round.
    """
    import time as _time

    now = _time.monotonic()
    if _EGRESS["ip"] and now - float(_EGRESS["at"]) < ttl:
        return _EGRESS["ip"]
    try:
        import httpx

        with httpx.Client(
            transport=httpx.HTTPTransport(local_address="0.0.0.0"), timeout=8
        ) as http:
            ip = http.get("https://api64.ipify.org").text.strip()
        if ip:
            _EGRESS["ip"] = ip
            _EGRESS["at"] = now
    except Exception:  # noqa: BLE001 - a diagnostic must never mask the real error
        pass
    return _EGRESS["ip"]


def _with_ip_hint(message: str) -> str:
    """Turn IIFL's opaque IP rejection into something you can act on.

    The whitelisted address is a SEBI requirement and the connection here is a
    dynamic consumer line, so this recurs whenever the ISP re-leases. Saying
    which address to register beats re-deriving it every time.
    """
    if "ip address not authorized" not in message.lower():
        return message
    ip = _egress_ip()
    if not ip:
        return message
    return (
        f"{message} This machine currently egresses as {ip} — register that "
        f"address at developers.iiflcapital.com (My Apps → View All Details → "
        f"Primary Static IP)."
    )
