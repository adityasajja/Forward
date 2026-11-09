"""Audit routes — ``/api/v1/audit``.

Reads the queryable table through :class:`~forward.services.audit.AuditService`, not
the JSONL file. The file remains the restart-proof sink that ``GET /audit``
(legacy) reads; this is the same facts with filters.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from forward.api.deps import require_permission
from forward.auth.rbac import Permission
from forward.config.settings import get_settings
from forward.wiring.audit import get_audit_service

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])

_READ = Depends(require_permission(Permission.AUDIT_READ))


@router.get("/actions", dependencies=[_READ])
def actions() -> dict[str, Any]:
    """Distinct action names, for building a filter."""
    return {"actions": get_audit_service().actions()}


@router.get("/events", dependencies=[_READ])
def events(
    user_id: str | None = None,
    action: str | None = None,
    result: str | None = Query(default=None, pattern="^(success|failure|denied|pending)$"),
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=get_settings().page_sizes["audit_default"], ge=1, le=get_settings().page_sizes["audit_max"]),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Filtered, newest-first page of audit events."""
    rows, total = get_audit_service().query(
        user_id=user_id,
        action=action,
        result=result,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    return {"events": rows, "total": total, "limit": limit, "offset": offset}


@router.get("/events/{event_id}", dependencies=[_READ])
def event(event_id: str) -> dict[str, Any]:
    row = get_audit_service().get(event_id)
    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"detail": "no such audit event", "code": "not_found"},
        )
    return row
