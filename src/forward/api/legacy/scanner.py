"""Momentum, full-market and custom scans, and saved scans."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from forward.api.deps import require_permission
from forward.api.legacy.common import _authed_client
from forward.auth.rbac import Permission
from forward.core.errors import MarketHistoryUnavailable
from forward.services.market_scanning import MarketScanningService
from forward.wiring.research import build_market_scanning_service, get_local_scan_service

router = APIRouter()

_SCAN = Depends(require_permission(Permission.SCREENER_RUN))


def _market_scanner() -> MarketScanningService:
    """Build a scanner for the authenticated IIFL market-data session."""
    return build_market_scanning_service(_authed_client())


@router.get("/scan")
def scan(
    symbols: str | None = None,
    service: MarketScanningService = Depends(_market_scanner),
) -> dict[str, Any]:
    """Momentum scan over liquid NSE names (real IIFL daily candles).

    Pass `?symbols=RELIANCE-EQ,INFY-EQ` to override the default universe.
    Takes ~30-60s for the full universe — the dashboard shows a spinner.
    """
    wanted = [s.strip().upper() for s in symbols.split(",") if s.strip()] if symbols else None
    rows, errors = service.scan(
        wanted,
        exchange="NSEEQ",
    )
    return {"as_of": date.today().isoformat(), "rows": rows, "errors": errors}


@router.get("/scan-all")
def scan_all(exchange: str = "NSEEQ") -> dict[str, Any]:
    """Full-market scan over the local history cache (`forward history sync`).

    No broker session needed and no API calls — scores all cached symbols in
    seconds. Reuses a recent result to make UI tab switches instant.
    """
    try:
        return get_local_scan_service().scan_all(exchange)
    except MarketHistoryUnavailable as error:
        raise HTTPException(503, "history cache is empty — run `forward history sync`") from error


# ---------------------------------------------------------------------------
# Custom condition-based scanner
# ---------------------------------------------------------------------------


class CustomCondition(BaseModel):
    """One comparison between a market series and a value or indicator."""

    indicator: str  # "rsi", "sma", "close", etc.
    period: int | None = None  # period for sma/ema/rsi/forward/bb_*
    op: str  # ">", "<", ">=", "<=", "=", "crosses_above", "crosses_below"
    rhs_type: str = "value"  # "value" | "indicator"
    rhs_value: float = 0.0  # used when rhs_type == "value"
    rhs_indicator: str | None = None  # used when rhs_type == "indicator"
    rhs_period: int | None = None  # used when rhs_type == "indicator"


class CustomScanRequest(BaseModel):
    """Request to run one custom scan over cached daily bars."""

    conditions: list[CustomCondition] = Field(default_factory=list)
    combine: str = "AND"  # "AND" | "OR"
    exchange: str = "NSEEQ"
    universe: str = "all"  # "all" | "watchlist"
    watchlist: list[str] = Field(default_factory=list)


@router.post("/scanner/custom", dependencies=[_SCAN])
def scanner_custom(body: CustomScanRequest) -> dict[str, Any]:
    """Evaluate user-defined indicator conditions over the local Parquet cache.

    No broker session required — runs entirely against the cached dailies.
    Returns matching symbols with standard score metrics + condition values.
    """
    if not body.conditions:
        raise HTTPException(400, "provide at least one condition")

    conds = [c.model_dump() for c in body.conditions]
    symbols = body.watchlist if body.universe == "watchlist" else None
    try:
        return get_local_scan_service().scan_custom(
            exchange=body.exchange,
            conditions=conds,
            combine=body.combine,
            symbols=symbols,
            use_default_universe=body.universe == "watchlist",
        )
    except MarketHistoryUnavailable as error:
        raise HTTPException(503, "history cache empty — run `forward history sync`") from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.get("/scanner/saved", dependencies=[_SCAN])
def scanner_saved_list() -> dict[str, Any]:
    """List all saved custom scans (includes built-in presets on first run)."""
    from forward.composition import get_saved_scan_service

    return {"scans": get_saved_scan_service().list_scans()}


class SavedScanUpsert(BaseModel):
    """Saved scan definition supplied by the operator."""

    id: str
    name: str
    combine: str = "AND"
    conditions: list[dict[str, Any]] = Field(default_factory=list)


@router.put("/scanner/saved/{scan_id}", dependencies=[_SCAN])
def scanner_saved_upsert(scan_id: str, body: SavedScanUpsert) -> dict[str, Any]:
    """Create or overwrite a saved scan."""
    from forward.composition import get_saved_scan_service

    entry = get_saved_scan_service().upsert(scan_id, body.model_dump())
    return {"saved": entry}


@router.delete("/scanner/saved/{scan_id}", dependencies=[_SCAN])
def scanner_saved_delete(scan_id: str) -> dict[str, Any]:
    """Delete a saved scan by id."""
    from forward.composition import get_saved_scan_service

    get_saved_scan_service().delete(scan_id)
    return {"deleted": scan_id}
