"""Candles, symbols, quotes and live ticks."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import date, datetime
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
)

from forward.api.deps import get_websocket_principal, require_principal_permission
from forward.api.legacy.common import (
    _authed_client,
    _clean,
    _with_ip_hint,
)
from forward.auth.rbac import Permission
from forward.composition import (
    build_chart_market_data_service,
)
from forward.core.errors import ContractCatalogUnavailable
from forward.services.instrument_catalog import InstrumentCatalogService
from forward.wiring.instrument_catalog import get_instrument_catalog_service

router = APIRouter()


@router.get("/candles")
def candles(
    symbol: str = "RELIANCE-EQ",
    exchange: str = "NSEEQ",
    interval: str = "1d",
    from_date: str = "01-Mar-2026",
    to_date: str | None = None,
) -> dict[str, Any]:
    """Raw OHLCV candles for charting. Interval accepts 1m/5m/15m/30m/60m/1d."""
    client = _authed_client()
    market_data = build_chart_market_data_service(client)
    try:
        candle_rows = market_data.candles(
            symbol,
            exchange,
            interval,
            from_date,
            to_date or date.today().strftime("%d-%b-%Y"),
        )
    except ContractCatalogUnavailable as exc:
        raise HTTPException(
            503, f"instrument cache is cold — run `forward instruments sync` ({exc})"
        ) from exc
    return {
        "symbol": symbol.upper(),
        "exchange": exchange.upper(),
        "interval": interval,
        "candles": candle_rows,
    }


@router.get("/symbols")
def symbols(
    query: str = "",
    exchange: str = "NSEEQ",
    limit: int = Query(25, ge=1, le=100),
    catalog: InstrumentCatalogService = Depends(get_instrument_catalog_service),
) -> dict[str, Any]:
    """Symbol search for the chart header. Served from the cached instrument
    master — no broker session needed."""
    try:
        hits = catalog.search(query.upper() or "EQ", exchange.upper(), limit)
    except ContractCatalogUnavailable as exc:
        raise HTTPException(
            503, f"instrument cache is cold — run `forward instruments sync` ({exc})"
        ) from exc
    return {
        "results": [
            {
                "symbol": instrument.symbol,
                "exchange": instrument.exchange,
                "conid": instrument.contract_id,
            }
            for instrument in hits
        ]
    }


@router.get("/quote")
def quote(
    symbols: str,
    exchange: str = "NSEEQ",
) -> dict[str, Any]:
    """Live quotes for a comma-separated symbol list, e.g. ``?symbols=RELIANCE-EQ,INFY-EQ``."""

    wanted = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not wanted:
        raise HTTPException(400, "pass ?symbols=RELIANCE-EQ,INFY-EQ")
    if len(wanted) > 50:
        raise HTTPException(400, "at most 50 symbols per request")

    client = _authed_client()
    market_data = build_chart_market_data_service(client)
    try:
        request = market_data.resolve_quotes(wanted, exchange)
    except ContractCatalogUnavailable as exc:
        raise HTTPException(
            503,
            f"no cached instrument master for {exchange} — run "
            f"`forward instruments sync --exchanges {exchange}` ({exc})",
        ) from exc

    if not request.instruments:
        raise HTTPException(404, f"could not resolve any of {wanted} on {exchange}")

    try:
        quote_batch = market_data.quote_payload(request.instruments)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"quote request failed: {exc}") from exc

    if quote_batch.error:
        raise HTTPException(502, _with_ip_hint(quote_batch.error))

    rows = quote_batch.rows
    for symbol, row in zip(request.symbols, rows, strict=False):
        row.setdefault("symbol", symbol)
    return {
        "exchange": exchange.upper(),
        "as_of": datetime.now().isoformat(),
        "quotes": _clean(rows),
        "failed": request.failed,
    }


def watchlist_live_quotes(
    symbols: list[str], exchange: str = "NSEEQ"
) -> dict[str, dict[str, Any]]:
    """Return the live quote overlay for every resolvable watchlist symbol.

    Args:
        symbols: Symbols in the requested watchlist.
        exchange: Exchange segment that contains those symbols.

    Returns:
        Live quotes indexed by canonical symbol. The route checks that every
        requested symbol has a valid last-traded price before returning data.

    Raises:
        HTTPException: If the broker request fails or no symbols resolve.
    """
    if len(symbols) > 50:
        raise HTTPException(400, "live quote requests support at most 50 symbols")

    market_data = build_chart_market_data_service(_authed_client())
    request = market_data.resolve_quotes(symbols, exchange)
    if not request.instruments:
        raise HTTPException(
            503,
            detail={
                "detail": f"no live quote instruments could be resolved for {exchange}",
                "code": "quotes_unavailable",
            },
        )

    quote_batch = market_data.quote_payload(request.instruments)
    if quote_batch.error:
        raise HTTPException(502, _with_ip_hint(quote_batch.error))

    return {
        symbol: dict(row)
        for symbol, row in zip(request.symbols, quote_batch.rows, strict=False)
        if isinstance(row, dict)
    }


@router.websocket("/ws/ticks")
async def ws_ticks(websocket: WebSocket) -> None:
    """Stream live ticks to an authenticated caller with market-read access."""
    import json

    # Browsers apply no CORS to websockets, so without this any page the operator
    # visits could open ws://localhost:8000/ws/ticks and drive the broker feed.
    from forward.api.middleware import origin_allowed
    from forward.api.stream import get_broadcaster

    origin = websocket.headers.get("origin")
    if origin and not origin_allowed(origin, websocket.headers.get("host")):
        await websocket.close(code=1008)
        return

    try:
        principal = get_websocket_principal(websocket)
        require_principal_permission(principal, Permission.MARKET_READ)
    except HTTPException as exc:
        close_code = 1008 if exc.status_code < 500 else 1011
        await websocket.close(code=close_code)
        return

    broadcaster = get_broadcaster()
    with suppress(RuntimeError):
        broadcaster.set_loop(asyncio.get_running_loop())

    await broadcaster.connect(websocket)
    try:
        while True:
            text = await websocket.receive_text()
            try:
                msg = json.loads(text)
                action = msg.get("action")
                symbols = msg.get("symbols", [])
                exchange = msg.get("exchange", "NSEEQ")
                if action == "subscribe" and isinstance(symbols, list):
                    await broadcaster.subscribe(websocket, symbols, exchange)
                elif action == "unsubscribe" and isinstance(symbols, list):
                    await broadcaster.unsubscribe(websocket, symbols)
            except Exception:
                pass
    except WebSocketDisconnect:
        broadcaster.disconnect(websocket)
    except Exception:
        broadcaster.disconnect(websocket)


@router.get("/ticks/history")
def get_tick_history(symbol: str = Query(..., description="Stock symbol"), limit: int = Query(100, ge=1, le=5000)) -> list[dict[str, Any]]:
    """Fetch raw buffered ticks from the in-memory time-series ring buffer."""
    from forward.api.stream import get_broadcaster
    return get_broadcaster().get_tick_history(symbol, limit=limit)


@router.get("/ticks/vwap")
def get_tick_vwap(symbol: str = Query(..., description="Stock symbol"), window: int = Query(900, ge=1, le=86400)) -> dict[str, Any]:
    """Compute instant rolling VWAP over `window` seconds from in-memory ring buffer."""
    from forward.api.stream import get_broadcaster
    return get_broadcaster().get_rolling_vwap(symbol, window_seconds=window)


@router.get("/ticks/candles")
def get_tick_candles(symbol: str = Query(..., description="Stock symbol"), interval: int = 5) -> list[dict[str, Any]]:
    """Resample buffered ticks into sub-minute OHLCV candles via Polars."""
    from forward.api.stream import get_broadcaster
    return get_broadcaster().get_tick_candles(symbol, interval_seconds=interval)
