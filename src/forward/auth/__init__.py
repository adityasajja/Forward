"""Authentication and authorization.

Layering: :mod:`forward.auth` owns identity and never imports the API layer. Routes
ask :class:`~forward.auth.service.AuthService` questions and never implement the
answers themselves.
"""

from __future__ import annotations

from forward.auth.models import LoginResult, Principal, anonymous_principal
from forward.auth.rbac import (
    MFA_REQUIRED_ROLES,
    Permission,
    PermissionDenied,
    Role,
    has_permission,
    permissions_for,
)

__all__ = [
    "MFA_REQUIRED_ROLES",
    "AuthError",
    "AuthService",
    "LoginResult",
    "Permission",
    "PermissionDenied",
    "Principal",
    "Role",
    "anonymous_principal",
    "has_permission",
    "permissions_for",
]

#: `forward.auth.service` pulls in cryptography (~450ms of import
#: time). Every submodule import here (`forward.auth.models`, `forward.auth.rbac`, ...)
#: runs this package's `__init__` first, so an eager import above paid that
#: cost on the path to routes that only wanted `Principal` or `Permission` and
#: never touch the auth service itself. PEP 562 module `__getattr__` keeps
#: `from forward.auth import AuthService` working unchanged, deferred to whoever
#: actually asks for it. The application service is built by the composition root.
def __getattr__(name: str):
    if name in ("AuthError", "AuthService"):
        from forward.auth import service

        return getattr(service, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
