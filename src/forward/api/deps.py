"""FastAPI dependencies for identity and authorization.

Every protected route depends on :func:`require_permission`, so the check lives
in the signature rather than in the body. A check in the body is a check someone
forgets to write; a check in the signature is one the framework refuses to skip.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, HTTPException, Request, WebSocket, status

from forward.auth.models import Principal, anonymous_principal
from forward.auth.rbac import Permission
from forward.config.settings import get_settings

if TYPE_CHECKING:
    # `forward.auth.service` pulls in sqlalchemy + cryptography (~450ms of import
    # time) — real cost for a route that just needs the type name. Deferred to
    # a lazy import at each call site below; this import is type-checking only
    # and never runs (postponed annotations mean the string is never evaluated).
    from forward.auth.service import AuthError

logger = logging.getLogger("forward.api.deps")

SESSION_COOKIE = "forward_session"


def client_ip(request: Request) -> str:
    """Best-effort client address.

    ``X-Forwarded-For`` is deliberately **not** trusted: nothing in this
    deployment terminates a proxy, and honouring a client-supplied header would
    let anyone forge the address that appears in the audit trail and that the
    rate limiter keys on.
    """
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def credential_from(request: Request | WebSocket) -> tuple[str | None, str]:
    """Extract a credential. Returns ``(value, source)``.

    Order matters: an explicit ``Authorization`` header wins over a cookie, so a
    script using a bearer token is never silently downgraded to whatever session
    the browser happens to hold.
    """
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip(), "bearer"

    api_key = request.headers.get("x-api-key")
    if api_key:
        return api_key.strip(), "api-key"

    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        return cookie, "cookie"

    return None, "none"


def _principal_from_credential(credential: str | None) -> Principal:
    """Resolve a credential and enforce the configured sign-in policy.

    Args:
        credential: Bearer token, API key, or session cookie value.
    Returns:
        The account principal for the credential.

    Raises:
        HTTPException: If auth storage is unavailable or the caller is unknown.
    """
    from forward.wiring.auth import get_auth_service

    try:
        principal = get_auth_service().resolve(credential)
    except Exception as exc:  # noqa: BLE001
        logger.warning("credential resolution failed: %s", exc)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="authentication store unavailable",
        ) from exc

    if principal is None and not credential and not get_settings().auth_required:
        return anonymous_principal()
    if principal is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail={"detail": "authentication required", "code": "unauthenticated"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal


def optional_principal(request: Request) -> Principal | None:
    """Resolve the caller, or None. Never raises — for routes that work either way."""
    from forward.wiring.auth import get_auth_service

    credential, _source = credential_from(request)
    service = get_auth_service()
    try:
        principal = service.resolve(credential)
    except Exception as exc:  # noqa: BLE001 - a broken store must not 500 /health
        logger.warning("credential resolution failed: %s", exc)
        return None
    if principal is not None:
        return principal
    if not credential and not get_settings().auth_required:
        return anonymous_principal()
    return None


def get_principal(request: Request) -> Principal:
    """The authenticated caller, or 401.

    A session that has not satisfied its second factor is still a valid
    principal — the enrolment gate lives in :func:`require_permission`, so such a
    session can reach ``/me`` and the MFA routes but nothing else.
    """
    credential, source = credential_from(request)
    principal = _principal_from_credential(credential)

    # Stash for the audit writer and the request-id header, so a route does not
    # have to re-derive who is calling.
    request.state.principal = principal
    request.state.auth_source = source
    return principal


def get_websocket_principal(websocket: WebSocket) -> Principal:
    """Resolve the WebSocket caller from its bearer token, API key, or cookie.

    Args:
        websocket: WebSocket handshake carrying the caller's credentials.

    Returns:
        The account principal for the connection.

    Raises:
        HTTPException: If auth storage is unavailable or the caller is unknown.
    """
    credential, _ = credential_from(websocket)
    return _principal_from_credential(credential)


def require_principal_permission(
    principal: Principal, permission: Permission | str
) -> Principal:
    """Check a permission for an already resolved principal.

    Args:
        principal: Authenticated account that made the request.
        permission: Permission required by the operation.

    Returns:
        The same principal after the permission check succeeds.

    Raises:
        HTTPException: If the account has not finished MFA enrolment or lacks
            the requested permission.
    """
    required = Permission.parse(permission)
    if not principal.mfa_satisfied:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={
                "detail": "finish two-factor enrolment before using the platform",
                "code": "mfa_enrolment_required",
            },
        )
    if not principal.can(required):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={
                "detail": f"role '{principal.role.value}' lacks permission '{required.value}'",
                "code": "permission_denied",
                "required": required.value,
            },
        )
    return principal


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]
OptionalPrincipal = Annotated[Principal | None, Depends(optional_principal)]


def require_permission(permission: Permission | str) -> Callable[..., Principal]:
    """Build a dependency that enforces one permission.

    Usage::

        @router.post("/", dependencies=[Depends(require_permission(Permission.WATCHLIST_WRITE))])

    or, when the route also needs the caller::

        def handler(principal: Principal = Depends(require_permission(Permission.ORDER_PLACE))):
    """
    required = Permission.parse(permission)

    def dependency(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
        return require_principal_permission(principal, required)

    return dependency


def auth_error_to_http(exc: AuthError) -> HTTPException:
    """Translate a service-level failure into a response.

    The machine-readable ``code`` is kept alongside the human message so a client
    can branch on the reason without matching English prose.
    """
    return HTTPException(
        exc.status,
        detail={"detail": str(exc), "code": exc.code},
    )


__all__ = [
    "SESSION_COOKIE",
    "CurrentPrincipal",
    "OptionalPrincipal",
    "auth_error_to_http",
    "client_ip",
    "credential_from",
    "get_principal",
    "get_websocket_principal",
    "optional_principal",
    "require_permission",
    "require_principal_permission",
]
