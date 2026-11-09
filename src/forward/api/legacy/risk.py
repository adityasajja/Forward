"""Kill switch, execution mode and the live-trading guards."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from forward.api.deps import require_permission
from forward.api.legacy.access import risk_state
from forward.api.legacy.common import _authed_client
from forward.auth.models import Principal
from forward.auth.rbac import Permission
from forward.config.settings import get_settings

logger = logging.getLogger("forward.api")

router = APIRouter()


@router.post("/risk/kill-switch")
def kill_switch(
    engaged: bool = True,
    reason: str = "",
    principal: Principal = Depends(require_permission(Permission.RISK_CONFIGURE)),
) -> dict[str, Any]:
    """Engage or release the global kill switch. Blocks all new orders until cleared.

    Rewired onto the durable state on 2026-09-14. It used to write an in-process
    dict, which had two consequences: a restart silently re-armed trading, and
    there was no single answer to "is the switch engaged?" because each route read
    the dict itself. The switch is now stored in ``system_state`` and read through
    ``RiskStateService``, which is also where the OMS risk gate gets it — so it
    applies to every order path rather than to the routes that remembered to ask.

    A reason is now required in **both** directions. Releasing is arguably the more
    consequential of the two: it re-enables trading, and a release with no recorded
    reason is indistinguishable from someone clearing it by accident.
    """
    from forward.composition import get_risk_state_service
    from forward.services.risk import RiskStateError

    # The actor is who authenticated, never a query parameter: a client-supplied
    # name would let anyone write any identity into the audit trail.
    actor = principal.username

    if not reason.strip():
        raise HTTPException(
            400,
            detail={
                "detail": (
                    "changing the kill switch requires a reason — it is recorded in the audit trail"
                ),
                "code": "reason_required",
            },
        )
    try:
        state = get_risk_state_service().set_kill_switch(engaged, reason=reason, actor=actor)
    except RiskStateError as exc:
        raise HTTPException(exc.status, detail={"detail": str(exc), "code": exc.code}) from exc

    from forward.wiring.audit import get_audit_log_service

    get_audit_log_service().append(
        actor=actor,
        action="kill_switch.engage" if engaged else "kill_switch.release",
        subject="global",
        detail=reason.strip(),
    )
    return {"kill_switch": state.kill_switch, "reason": reason.strip()}


@router.get("/risk/execution-mode", dependencies=[Depends(require_permission(Permission.RISK_READ))])
def get_execution_mode() -> dict[str, Any]:
    """Whether orders reach the broker, or are only recorded.

    `paper` is the safe default: signals are generated and graded exactly as in
    live, the order path is exercised up to the broker boundary, but nothing is
    transmitted. This is enforced in the execution service, not merely shown here
    — a toggle that only changes a label is worse than none, because it invites
    you to trust it.
    """
    return risk_state()


@router.post("/risk/execution-mode")
def set_execution_mode(
    mode: str,
    reason: str = "",
    principal: Principal = Depends(require_permission(Permission.EXECUTION_MODE_CHANGE)),
) -> dict[str, Any]:
    """Switch between `paper` and `live`.

    Going *live* requires an explicit reason. That is deliberate friction: the
    transition that can lose real money should cost a sentence, and the
    sentence is what shows up in the audit trail later. Coming back to paper does
    not require one — reducing risk must never be harder than taking it on.
    """
    from forward.composition import get_risk_state_service
    from forward.services.risk import RiskStateError

    actor = principal.username

    if mode not in {"paper", "live"}:
        raise HTTPException(400, "mode must be 'paper' or 'live'")
    if mode == "live" and not reason.strip():
        raise HTTPException(
            400,
            detail={
                "detail": (
                    "Switching to live requires a reason — it is recorded in the audit trail"
                ),
                "code": "reason_required",
            },
        )

    previous = risk_state()["mode"]
    try:
        get_risk_state_service().set_execution_mode(mode, reason=reason, actor=actor)
    except RiskStateError as exc:
        raise HTTPException(exc.status, detail={"detail": str(exc), "code": exc.code}) from exc

    from forward.wiring.audit import get_audit_log_service

    get_audit_log_service().append(
        actor=actor,
        action="execution_mode.change",
        subject=f"{previous} -> {mode}",
        detail=reason.strip() or None,
    )
    logger.warning(
        "execution mode %s -> %s by %s (%s)", previous, mode, actor, reason.strip() or "no reason"
    )
    return risk_state()


@router.get("/risk/status", dependencies=[Depends(require_permission(Permission.RISK_READ))])
def risk_status() -> dict[str, Any]:
    """Kill switch plus the limits that actually gate order placement.

    Reads the *live* settings rather than restating a config default, so the
    panel cannot drift from what the engine enforces — a risk panel showing a
    stale limit is worse than no panel.
    """
    from forward.wiring.alerts import get_trade_signal_settings
    from forward.wiring.broker_data import build_broker_account_read_service

    state = risk_state()
    settings = get_settings()
    ts = get_trade_signal_settings().load()

    # Broker-reported margin is best-effort: a dead session must not blank the
    # panel, because the kill switch is exactly what you reach for when things
    # are broken.
    margin: dict[str, Any] = {}
    margin_error: str | None = None
    try:
        client = _authed_client()
        result = build_broker_account_read_service(client).read("limits")
        row = result.rows[0] if not result.error and result.rows else {}
        for key in (
            "availableMargin",
            "marginUtilized",
            "collateralValue",
            "openingCashLimit",
            "intradayPayinAmount",
            "creditForSellAmount",
            "blockedForPayoutAmount",
            "utilizedAmount",
            "net",
        ):
            if key in row:
                margin[key] = row[key]
    except Exception as exc:  # noqa: BLE001 — report, don't fail the panel
        margin_error = str(exc)

    return {
        "kill_switch": bool(state.get("kill_switch")),
        "execution_mode": str(state.get("execution_mode", "paper")),
        "env": settings.env,
        "live_orders_allowed": settings.env in {"paper", "live"},
        "limits": {
            "capital": ts.capital,
            "risk_per_trade_pct": ts.risk_per_trade_pct,
            "max_active": ts.max_active,
            "rr_ratio": ts.rr_ratio,
            "stop_method": ts.stop_method,
            "stop_atr_mult": ts.stop_atr_mult,
            "stop_pct": ts.stop_pct,
            "product": ts.product,
        },
        "margin": margin,
        "margin_error": margin_error,
    }


# ---------------------------------------------------------------------------
# Semi-automatic trade signals
# ---------------------------------------------------------------------------
# Self-learning quantitative engine
# ---------------------------------------------------------------------------
