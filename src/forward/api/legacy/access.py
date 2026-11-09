"""Shared authentication and execution checks for legacy API routes."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request, status

from forward.auth.models import Principal


def risk_state() -> dict[str, Any]:
    """Read execution mode and kill-switch state from durable storage.

    Returns:
        Execution state fields used by legacy API responses.
    """
    from forward.composition import get_risk_state_service

    try:
        snapshot = get_risk_state_service().snapshot()
    except Exception as exc:  # noqa: BLE001 - an unavailable risk state cannot be guessed
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"detail": "risk state is unavailable", "code": "risk_state_unavailable"},
        ) from exc
    return {
        "mode": snapshot.execution_mode,
        "live": snapshot.live,
        "paper": not snapshot.live,
        "changed_at": snapshot.changed_at,
        "changed_by": snapshot.changed_by,
        "reason": snapshot.reason,
        "execution_mode": snapshot.execution_mode,
        "kill_switch": snapshot.kill_switch,
        "mode_changed_at": snapshot.changed_at,
        "mode_changed_by": snapshot.changed_by,
        "mode_reason": snapshot.reason,
    }


def live_broker() -> Any:
    """Return the broker configured to place live orders.

    Returns:
        The authenticated live broker.

    Raises:
        HTTPException: If live broker access is unavailable.
    """
    from forward.core.errors import BrokerUnavailable
    from forward.wiring.broker_access import get_live_broker

    try:
        return get_live_broker()
    except BrokerUnavailable as error:
        raise HTTPException(error.status, str(error)) from error


def order_principal(request: Request) -> Principal:
    """Require an account that can own an order.

    Args:
        request: Incoming HTTP request with the current session.

    Returns:
        The authenticated account principal.

    Raises:
        HTTPException: If no signed-in account can own the order.
    """
    from forward.api.deps import optional_principal

    principal = optional_principal(request)
    if principal is None or not getattr(principal, "user_id", None):
        raise HTTPException(
            401,
            detail={
                "detail": (
                    "placing an order requires a platform account — sign in first. "
                    "Read-only routes remain available without one."
                ),
                "code": "authentication_required_for_orders",
            },
        )
    return principal


def require_order_permission(principal: Principal) -> None:
    """Require MFA and permission to place an order.

    Args:
        principal: Account that requested the order.

    Raises:
        HTTPException: If MFA or order permission is missing.
    """
    from fastapi import status

    from forward.auth.rbac import Permission

    if not principal.mfa_satisfied:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={
                "detail": "finish two-factor enrolment before using the platform",
                "code": "mfa_enrolment_required",
            },
        )
    if not principal.can(Permission.ORDER_PLACE):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={
                "detail": (
                    f"role '{principal.role.value}' lacks permission "
                    f"'{Permission.ORDER_PLACE.value}'"
                ),
                "code": "permission_denied",
                "required": Permission.ORDER_PLACE.value,
            },
        )


def require_live_execution(action: str = "order") -> None:
    """Require live mode before a legacy route sends an order.

    Args:
        action: Name shown in the error when live mode is off.

    Raises:
        HTTPException: If the stored execution mode is not live.
    """
    state = risk_state()
    if state["execution_mode"] != "live":
        raise HTTPException(
            403,
            f"{action} blocked: execution mode is 'paper'. "
            "Switch to live in Trading → Execution mode (a reason is required).",
        )
