"""Price and intelligent alerts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, get_args

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from forward.alerts.models import RuleKind
from forward.api.deps import require_permission
from forward.api.legacy.common import _authed_client
from forward.auth.rbac import Permission
from forward.config.settings import get_settings

router = APIRouter()
_CONFIGURE = Depends(require_permission(Permission.SYSTEM_CONFIGURE))


class AlertRuleIn(BaseModel):
    name: str = ""
    symbol: str
    exchange: str
    kind: RuleKind
    threshold: float
    cooldown_min: int
    armed: bool


@router.get("/alerts/rules", dependencies=[_CONFIGURE])
def alert_rules() -> list[dict[str, Any]]:
    from forward.wiring.alerts import get_alert_service

    return [rule.model_dump(mode="json") for rule in get_alert_service().rules()]


@router.get("/alerts/rules/kinds", dependencies=[Depends(require_permission(Permission.MARKET_READ))])
def alert_rule_kinds() -> list[str]:
    """Return the rule kinds accepted by the alert service."""
    return list(get_args(RuleKind))


@router.post("/alerts/rules", dependencies=[_CONFIGURE])
def alert_create(body: AlertRuleIn) -> dict[str, Any]:
    from forward.wiring.alerts import get_alert_service

    return get_alert_service().save_rule(body.model_dump()).model_dump(mode="json")


@router.patch("/alerts/rules/{rule_id}", dependencies=[_CONFIGURE])
def alert_arm(rule_id: str, armed: bool = True) -> dict[str, Any]:
    from forward.wiring.alerts import get_alert_service

    rule = get_alert_service().arm_rule(rule_id, armed)
    if rule is None:
        raise HTTPException(404, f"no such rule: {rule_id}")
    return rule.model_dump(mode="json")


@router.delete("/alerts/rules/{rule_id}", dependencies=[_CONFIGURE])
def alert_delete(rule_id: str) -> dict[str, bool]:
    from forward.wiring.alerts import get_alert_service

    if not get_alert_service().remove_rule(rule_id):
        raise HTTPException(404, f"no such rule: {rule_id}")
    return {"deleted": True}


@router.get("/alerts/events", dependencies=[_CONFIGURE])
def alert_events(
    limit: int = Query(
        get_settings().page_sizes["alert_events_default"],
        ge=1,
        le=get_settings().page_sizes["alert_events_max"],
    ),
) -> list[dict[str, Any]]:
    from forward.wiring.alerts import get_alert_service

    return [e.model_dump(mode="json") for e in get_alert_service().events(limit)]


@router.post("/alerts/check", dependencies=[_CONFIGURE])
def alert_check() -> dict[str, Any]:
    """Evaluate all armed rules right now. Returns what fired."""
    from forward.wiring.alerts import get_alert_quote_port, get_alert_service

    market_open, events = get_alert_service().check(get_alert_quote_port(_authed_client()))
    return {
        "market_open": market_open,
        "fired": [e.model_dump(mode="json") for e in events],
    }


@router.post("/alerts/test", dependencies=[_CONFIGURE])
def alert_test() -> dict[str, Any]:
    """Send a Forward test alert through the configured channels."""
    from forward.wiring.alerts import get_alert_service

    return {"sent_on": get_alert_service().send_test()}


# ----------------------------------------------------------------------
# Intelligent Automated Buy & Sell Alerts
# ----------------------------------------------------------------------
@router.get("/alerts/intelligent/config", dependencies=[_CONFIGURE])
def get_intelligent_alert_config() -> dict[str, Any]:
    from forward.wiring.alerts import get_intelligent_alert_monitor

    monitor = get_intelligent_alert_monitor()
    cfg = monitor.get_config()
    status = monitor.get_status()
    return {
        "config": cfg.model_dump(mode="json"),
        "status": status,
    }


@router.post("/alerts/intelligent/config", dependencies=[_CONFIGURE])
def update_intelligent_alert_config(body: dict[str, Any]) -> dict[str, Any]:
    from forward.wiring.alerts import get_intelligent_alert_monitor

    monitor = get_intelligent_alert_monitor()
    new_cfg = monitor.update_config(body)
    return {
        "config": new_cfg.model_dump(mode="json"),
        "status": monitor.get_status(),
    }


@router.post("/alerts/intelligent/evaluate", dependencies=[_CONFIGURE])
async def evaluate_intelligent_alerts_now() -> dict[str, Any]:
    """Force an immediate evaluation cycle across target stocks right now."""
    from forward.wiring.alerts import get_intelligent_alert_monitor

    monitor = get_intelligent_alert_monitor()
    signals = await monitor.run_evaluation_cycle(force=True)
    return {
        "signals": [s.model_dump(mode="json") for s in signals],
        "count": len(signals),
        "as_of": datetime.now().isoformat(),
        "status": monitor.get_status(),
    }
