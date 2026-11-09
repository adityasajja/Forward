"""Data provenance and freshness — ``/api/v1/data``.

Read-only. Says where each stored data set comes from and how many trading sessions behind
the market it is, so no page has to leave a person guessing whether a figure is current.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from forward.api.deps import require_permission
from forward.auth.models import Principal
from forward.auth.rbac import Permission
from forward.composition import get_data_status

router = APIRouter(prefix="/api/v1/data", tags=["data"])

_READ = require_permission(Permission.MARKET_READ)


@router.get("/status")
def data_status(_: Principal = Depends(_READ)) -> dict[str, Any]:
    return get_data_status()
