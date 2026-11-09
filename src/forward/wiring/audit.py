"""Wire audit use cases to their durable storage adapters."""

from __future__ import annotations

from functools import lru_cache

from forward.appdb.audit_storage import SqlAuditStorage
from forward.appdb.engine import get_app_db
from forward.config.settings import audit_log_path
from forward.infra.audit_log import JsonlAuditLogAdapter
from forward.services.audit import AuditService
from forward.services.audit_log import AuditLogService

_AUDIT_SERVICE = AuditService(SqlAuditStorage(get_app_db()))


def get_audit_service() -> AuditService:
    """Return the audit query service with its SQL storage adapter."""
    return _AUDIT_SERVICE


def get_audit_log_service() -> AuditLogService:
    """Return the service for the local JSONL audit trail."""
    return AuditLogService(get_audit_log_adapter())


@lru_cache(maxsize=1)
def get_audit_log_adapter() -> JsonlAuditLogAdapter:
    """Build the JSONL writer from the configured audit path."""
    return JsonlAuditLogAdapter(audit_log_path())


def reset_audit_log_adapter() -> None:
    """Clear the cached file adapter after settings change."""
    get_audit_log_adapter.cache_clear()
