"""Dashboard summary and portfolio HTTP routes."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from forward.api.deps import require_permission
from forward.api.legacy.common import _authed_client, _clean, _with_ip_hint
from forward.auth.rbac import Permission
from forward.composition import (
    get_dashboard_summary_service,
)
from forward.services.broker_account_read import parse_account_sections
from forward.wiring.broker_data import build_broker_account_read_service

router = APIRouter()


@router.get("/dashboard/summary", dependencies=[Depends(require_permission(Permission.ORDER_READ))])
def dashboard_summary(exchange: str = "NSEEQ") -> dict[str, Any]:
    """Return position P&L, trade performance, and market breadth."""
    return get_dashboard_summary_service().summary(
        lambda: build_broker_account_read_service(_authed_client()),
        exchange,
    )


@router.get("/portfolio", dependencies=[Depends(require_permission(Permission.ORDER_READ))])
def portfolio(sections: str | None = None) -> dict[str, Any]:
    """Read requested broker account sections for the dashboard."""
    try:
        parse_account_sections(sections)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    account = build_broker_account_read_service(_authed_client())
    section_data = account.sections(sections)
    for section in section_data.values():
        if "error" in section:
            section["error"] = _with_ip_hint(section["error"])
        if "rows" in section:
            section["rows"] = _clean(section["rows"])
    return {"sections": section_data, "as_of": datetime.now().isoformat()}
