"""Daily profit-and-loss calendars: one for paper trades, one for the real book."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from forward.api.deps import get_principal
from forward.auth.models import Principal
from forward.services.pnl_calendar import PnlCalendarInputError

router = APIRouter()


class RealisedIn(BaseModel):
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    amount: float = Field(
        ge=-1e9,
        le=1e9,
        description="Profit (or loss, negative) made by trading that day, in rupees",
    )
    note: str = Field("", max_length=200)


@router.get("/pnl/calendar")
def pnl_calendar_month(
    scope: Literal["paper", "real"],
    principal: Principal = Depends(get_principal),
    month: str | None = Query(
        None,
        pattern=r"^\d{4}-\d{2}$",
        description="YYYY-MM; defaults to the latest month with data",
    ),
) -> dict[str, Any]:
    """One month of daily P&L. ``paper`` adds up every paper trade; ``real`` is the holdings' daily change."""
    try:
        from forward.composition import build_pnl_calendar_service

        return build_pnl_calendar_service().month(scope, month, principal.user_id)
    except PnlCalendarInputError as error:
        raise HTTPException(400, str(error)) from error


@router.get("/pnl/day")
def pnl_day(
    scope: Literal["paper", "real"],
    principal: Principal = Depends(get_principal),
    day: str = Query(..., alias="date", pattern=r"^\d{4}-\d{2}-\d{2}$"),
) -> dict[str, Any]:
    """What is behind one calendar day: the trades that closed (paper) or the holdings that moved (real)."""
    try:
        from forward.composition import build_pnl_calendar_service

        return build_pnl_calendar_service().day(scope, day, principal.user_id)
    except PnlCalendarInputError as error:
        raise HTTPException(400, str(error)) from error


@router.put("/pnl/realised")
def record_trading_profit(
    body: RealisedIn, principal: Principal = Depends(get_principal)
) -> dict[str, Any]:
    """Record what trading made (or lost) on a day. Replaces any earlier entry for that day."""
    try:
        from forward.composition import build_pnl_calendar_service

        return build_pnl_calendar_service().record_realised(
            principal.user_id, body.date, body.amount, body.note
        )
    except PnlCalendarInputError as error:
        raise HTTPException(400, str(error)) from error


@router.delete("/pnl/realised", dependencies=[Depends(get_principal)])
def remove_trading_profit(
    principal: Principal = Depends(get_principal),
    day: str = Query(..., alias="date", pattern=r"^\d{4}-\d{2}-\d{2}$"),
) -> dict[str, bool]:
    from forward.composition import build_pnl_calendar_service

    return build_pnl_calendar_service().remove_realised(principal.user_id, day)
