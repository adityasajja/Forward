"""Instrument master routes — ``/api/v1/instruments``."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from forward.api.deps import require_permission
from forward.auth.rbac import Permission
from forward.composition import get_instrument_directory_service
from forward.config.settings import get_settings

router = APIRouter(prefix="/api/v1/instruments", tags=["instruments"])

_READ = Depends(require_permission(Permission.INSTRUMENT_READ))


@router.get("/search", dependencies=[_READ])
def search(
    q: str = Query(min_length=1, max_length=64),
    exchange: str | None = None,
    asset_class: str | None = None,
    limit: int = Query(default=get_settings().page_sizes["instrument_search_default"], ge=1, le=get_settings().page_sizes["instrument_search_max"]),
) -> dict[str, Any]:
    """Ranked symbol search.

    Accepts any spelling — ``RELIANCE``, ``reliance``, ``RELIANCE-EQ``,
    ``NIFTY 50`` — and returns the same canonical record. Canonicalisation is
    this service's job, not every caller's.
    """
    results = get_instrument_directory_service().search(
        q, exchange=exchange, asset_class=asset_class, limit=limit
    )
    return {
        "query": q,
        "count": len(results),
        "results": results,
    }


@router.get("/exchanges", dependencies=[_READ])
def exchanges() -> dict[str, Any]:
    return {"exchanges": get_instrument_directory_service().exchanges()}


@router.get("/status", dependencies=[_READ])
def status_() -> dict[str, Any]:
    """Provenance of the master: how many symbols, how fresh, built from where."""
    return get_instrument_directory_service().status()


@router.post("/refresh", dependencies=[_READ])
def refresh() -> dict[str, Any]:
    """Rebuild the index from the local cache.

    Takes ~8s across 3,000+ files, which is why the result is persisted and only
    rebuilt when the cache fingerprint changes.
    """
    return get_instrument_directory_service().refresh()


@router.get("/universes", dependencies=[_READ])
def universes() -> dict[str, Any]:
    """Index membership loaded from ``data/universe/``."""
    master = get_instrument_directory_service()
    return {
        "universes": {name: len(members) for name, members in master.universes().items()},
    }


@router.get("/universes/{name}", dependencies=[_READ])
def universe(name: str) -> dict[str, Any]:
    members = get_instrument_directory_service().universe(name)
    if not members:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"detail": f"unknown universe {name!r}", "code": "not_found"},
        )
    return {"name": name, "count": len(members), "symbols": members}


@router.get("/{symbol}", dependencies=[_READ])
def get_instrument(symbol: str) -> dict[str, Any]:
    record = get_instrument_directory_service().get(symbol)
    if record is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"detail": f"unknown symbol {symbol!r}", "code": "not_found"},
        )
    return record
