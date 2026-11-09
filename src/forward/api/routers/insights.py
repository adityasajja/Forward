"""``/api/v1/insights``: the day's read, and the switch for sending it."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from forward.api.deps import require_permission
from forward.auth.models import Principal
from forward.auth.rbac import Permission
from forward.composition import get_insights_service
from forward.insights.service import InsightsSettings

router = APIRouter(prefix="/api/v1/insights", tags=["insights"])

_READ = require_permission(Permission.MARKET_READ)
_CONFIG = require_permission(Permission.SYSTEM_CONFIGURE)


def _client() -> Any:
    """Return the authenticated broker client required for live holdings."""
    from forward.wiring.broker_access import get_authenticated_broker_client

    return get_authenticated_broker_client()


@router.get("/today")
def today(
    fresh: bool = Query(False, description="Skip the two-minute cache"),
    principal: Principal = Depends(_READ),
) -> dict[str, Any]:
    digest = get_insights_service().build(user_id=principal.user_id, client=_client(), fresh=fresh)
    return {k: v for k, v in digest.items() if not k.startswith("_")}


@router.post("/send")
def send_now(principal: Principal = Depends(_CONFIG)) -> dict[str, Any]:
    """Send today's read to Telegram now, whether or not the daily one has gone out."""
    result = get_insights_service().send(user_id=principal.user_id, client=_client(), force=True)
    return {"sent": result["sent"], "channel": result.get("channel")}


@router.get("/settings")
def get_settings_(principal: Principal = Depends(_READ)) -> dict[str, Any]:
    return get_insights_service().settings().model_dump()


@router.put("/settings")
def put_settings(body: InsightsSettings, principal: Principal = Depends(_CONFIG)) -> dict[str, Any]:
    return get_insights_service().save_settings(body).model_dump()
