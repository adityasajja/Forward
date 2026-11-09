"""Watchlist routes — ``/api/v1/watchlists``.

Every route is user-scoped. The service filters on ``user_id`` rather than
checking after the fact, so a watchlist id belonging to someone else behaves
exactly like one that does not exist (404, never 403).
"""

from __future__ import annotations

import math
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from forward.api.deps import CurrentPrincipal, require_permission
from forward.auth.rbac import Permission
from forward.composition import get_watchlist_service
from forward.services.watchlists import WatchlistError, WatchlistService

router = APIRouter(prefix="/api/v1/watchlists", tags=["watchlists"])

_WRITE = Depends(require_permission(Permission.WATCHLIST_WRITE))
_READ = Depends(require_permission(Permission.WATCHLIST_READ))


def _service() -> WatchlistService:
    return get_watchlist_service()


def _fail(exc: WatchlistError) -> HTTPException:
    return HTTPException(exc.status, detail={"detail": str(exc), "code": exc.code})


def _not_found() -> HTTPException:
    return HTTPException(
        status.HTTP_404_NOT_FOUND,
        detail={"detail": "no such watchlist", "code": "not_found"},
    )


# --------------------------------------------------------------------- models
class WatchlistCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    exchange: str = Field(default="NSEEQ", max_length=16)
    columns: list[str] | None = None


class WatchlistUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=64)
    exchange: str | None = Field(default=None, max_length=16)
    is_default: bool | None = None


class ItemsAdd(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=500)


class ItemsOrder(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=500)


class ColumnsSet(BaseModel):
    columns: list[str] = Field(min_length=1, max_length=64)


# --------------------------------------------------------------------- routes
@router.get("/columns/available", dependencies=[_READ])
def available_columns() -> dict[str, Any]:
    """The column registry.

    Includes columns that cannot be populated yet, each with the reason. The UI
    renders them as unavailable rather than as zero — a zero looks like a
    measurement, and this platform does not do that.
    """
    specs = WatchlistService.available_columns()
    return {
        "columns": specs,
        "groups": sorted({s["group"] for s in specs}),
        "available": sorted(s["key"] for s in specs if s["available"]),
        "unavailable": sorted(s["key"] for s in specs if not s["available"]),
    }


@router.get("", dependencies=[_READ])
def list_watchlists(principal: CurrentPrincipal) -> dict[str, Any]:
    return {"watchlists": _service().list_watchlists(principal.user_id)}


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[_WRITE])
def create_watchlist(payload: WatchlistCreate, principal: CurrentPrincipal) -> dict[str, Any]:
    try:
        return _service().create(
            principal.user_id,
            name=payload.name,
            exchange=payload.exchange,
            columns=payload.columns,
        )
    except WatchlistError as exc:
        raise _fail(exc) from exc


@router.get("/{watchlist_id}", dependencies=[_READ])
def get_watchlist(watchlist_id: str, principal: CurrentPrincipal) -> dict[str, Any]:
    row = _service().get(principal.user_id, watchlist_id)
    if row is None:
        raise _not_found()
    return row


@router.patch("/{watchlist_id}", dependencies=[_WRITE])
def update_watchlist(
    watchlist_id: str, payload: WatchlistUpdate, principal: CurrentPrincipal
) -> dict[str, Any]:
    fields = payload.model_dump(exclude_none=True)
    if not fields:
        row = _service().get(principal.user_id, watchlist_id)
        if row is None:
            raise _not_found()
        return row
    try:
        if fields.get("is_default"):
            row = _service().set_default(principal.user_id, watchlist_id)
        else:
            fields.pop("is_default", None)
            row = _service().update(principal.user_id, watchlist_id, **fields)
    except WatchlistError as exc:
        raise _fail(exc) from exc
    if row is None:
        raise _not_found()
    return row


@router.delete("/{watchlist_id}", dependencies=[_WRITE])
def delete_watchlist(watchlist_id: str, principal: CurrentPrincipal) -> dict[str, Any]:
    if not _service().delete(principal.user_id, watchlist_id):
        raise _not_found()
    return {"deleted": True, "watchlist_id": watchlist_id}


@router.post("/{watchlist_id}/items", dependencies=[_WRITE])
def add_items(watchlist_id: str, payload: ItemsAdd, principal: CurrentPrincipal) -> dict[str, Any]:
    """Add symbols. Unknown tickers are reported, not silently dropped."""
    try:
        return _service().add_items(principal.user_id, watchlist_id, payload.symbols)
    except WatchlistError as exc:
        raise _fail(exc) from exc


@router.delete("/{watchlist_id}/items/{symbol}", dependencies=[_WRITE])
def remove_item(watchlist_id: str, symbol: str, principal: CurrentPrincipal) -> dict[str, Any]:
    if not _service().remove_item(principal.user_id, watchlist_id, symbol):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"detail": "symbol not in this watchlist", "code": "not_found"},
        )
    return {"removed": symbol.upper()}


@router.put("/{watchlist_id}/items/order", dependencies=[_WRITE])
def reorder_items(
    watchlist_id: str, payload: ItemsOrder, principal: CurrentPrincipal
) -> dict[str, Any]:
    """Set the full order. Idempotent, so a retry cannot reorder twice."""
    try:
        row = _service().reorder(principal.user_id, watchlist_id, payload.symbols)
    except WatchlistError as exc:
        raise _fail(exc) from exc
    if row is None:
        raise _not_found()
    return row


@router.put("/{watchlist_id}/columns", dependencies=[_WRITE])
def set_columns(
    watchlist_id: str, payload: ColumnsSet, principal: CurrentPrincipal
) -> dict[str, Any]:
    try:
        row = _service().set_columns(principal.user_id, watchlist_id, payload.columns)
    except WatchlistError as exc:
        raise _fail(exc) from exc
    if row is None:
        raise _not_found()
    return row


@router.get("/{watchlist_id}/quotes", dependencies=[_READ])
def watchlist_quotes(
    watchlist_id: str, request: Request, principal: CurrentPrincipal, live: bool = True
) -> dict[str, Any]:
    """Every configured column for every symbol.

    ``live=true`` requires a live quote for every symbol. Use ``live=false`` to
    request local cached data explicitly.
    """
    service = _service()
    detail = service.get(principal.user_id, watchlist_id)
    if detail is None:
        raise _not_found()

    overlay: dict[str, dict[str, Any]] = {}
    if live and detail["items"]:
        provider = getattr(request.app.state, "live_quote_provider", None)
        if provider is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"detail": "live quote provider is not configured", "code": "quotes_unavailable"},
            )
        try:
            overlay = provider(detail["items"], detail["exchange"])
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"detail": f"live quotes are unavailable: {exc}", "code": "quotes_unavailable"},
            ) from exc
        if not isinstance(overlay, dict):
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"detail": "live quote provider returned an invalid response", "code": "quotes_unavailable"},
            )
        missing = sorted(set(detail["items"]) - overlay.keys())
        if missing:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "detail": "live quotes are missing for one or more symbols",
                    "code": "quotes_incomplete",
                    "symbols": missing,
                },
            )
        invalid = sorted(
            symbol
            for symbol in detail["items"]
            if not _has_valid_ltp(overlay[symbol])
        )
        if invalid:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "detail": "live quotes have no valid last-traded price",
                    "code": "quotes_incomplete",
                    "symbols": invalid,
                },
            )

    result = service.quotes(principal.user_id, watchlist_id, live=overlay)
    if result is None:
        raise _not_found()
    return result


def _has_valid_ltp(quote: Any) -> bool:
    """Return whether a live quote carries a finite, positive last price."""
    if not isinstance(quote, dict):
        return False
    price = quote.get("ltp")
    return (
        isinstance(price, (int, float))
        and not isinstance(price, bool)
        and math.isfinite(price)
        and price > 0
    )
