"""Build authentication and request-limiting services."""

from __future__ import annotations

from functools import lru_cache

from forward.appdb.auth_storage import SqlAuthStorageAdapter
from forward.appdb.engine import get_app_db
from forward.auth.rate_limit import RateLimiter
from forward.auth.service import AuthService
from forward.config.settings import get_settings, secret_key_path
from forward.infra.secret_box_provider import ConfiguredSecretBoxProvider
from forward.wiring.audit import get_audit_log_adapter


@lru_cache(maxsize=1)
def get_auth_service() -> AuthService:
    """Build and cache authentication with its SQL storage adapter."""
    settings = get_settings()
    secret_box_provider = ConfiguredSecretBoxProvider(
        key_material=settings.forward_secret_key,
        key_file=secret_key_path(),
    )
    return AuthService(
        SqlAuthStorageAdapter(get_app_db(), get_audit_log_adapter()),
        settings,
        secret_box_provider,
    )


def reset_auth_service() -> None:
    """Clear authentication after its settings or database changes."""
    get_auth_service.cache_clear()


@lru_cache(maxsize=1)
def get_rate_limiter() -> RateLimiter:
    """Build the process-wide request limiter from configured policy."""
    return RateLimiter(default_per_minute=get_settings().rate_limit_per_minute)


def reset_rate_limiter() -> None:
    """Clear the cached limiter after its settings change."""
    get_rate_limiter.cache_clear()
