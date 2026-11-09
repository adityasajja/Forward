"""Health, audit log and data-status endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from forward.api.deps import require_permission
from forward.api.legacy.access import risk_state
from forward.auth.rbac import Permission
from forward.composition import (
    get_database_health_service,
    get_market_cache_status_service,
)
from forward.config.settings import get_settings
from forward.services.broker_session import BrokerSessionService
from forward.services.database_health import DatabaseHealthService
from forward.services.market_cache_status import MarketCacheStatusService
from forward.wiring.audit import get_audit_log_service
from forward.wiring.broker_data import get_broker_session_service

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    env: str
    database: bool
    session_active: bool
    # The environment banner reads these. They live on /health rather than a
    # separate call so the banner can never disagree with the rest of the
    # header, and so it refreshes on the existing poll.
    execution_mode: str = "paper"
    kill_switch: bool = False


@router.get("/health", response_model=HealthResponse)
def health(
    session_service: BrokerSessionService = Depends(get_broker_session_service),
    database_service: DatabaseHealthService = Depends(get_database_health_service),
) -> HealthResponse:
    settings = get_settings()
    database = database_service.is_available()
    session_active = bool(session_service.status()["session_active"])
    risk = risk_state()
    return HealthResponse(
        status="ok",
        env=settings.env,
        database=database,
        session_active=session_active,
        execution_mode=str(risk.get("execution_mode", "paper")),
        kill_switch=bool(risk.get("kill_switch")),
    )


@router.get(
    "/audit",
    dependencies=[Depends(require_permission(Permission.AUDIT_READ))],
)
def get_audit(
    limit: int = Query(
        get_settings().page_sizes["audit_log"],
        ge=1,
        le=get_settings().page_sizes["audit_log_max"],
    ),
) -> dict[str, Any]:
    """The immutable trail, newest first."""
    audit_log = get_audit_log_service()
    return {"entries": audit_log.recent(limit), "path": audit_log.path()}


# ----------------------------------------------------------------------
# Local caches — what the engine has to work with offline
# ----------------------------------------------------------------------
@router.get("/history/status")
def history_status(
    cache_status: MarketCacheStatusService = Depends(get_market_cache_status_service),
) -> dict[str, Any]:
    """Coverage of the local daily cache. No broker session required."""
    return cache_status.daily_history()


@router.get("/instruments/status")
def instruments_status(
    cache_status: MarketCacheStatusService = Depends(get_market_cache_status_service),
) -> dict[str, Any]:
    """Cached contract-file coverage per segment. No broker session required."""
    return cache_status.contracts()
