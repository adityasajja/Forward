"""The forward test of the oversold-and-volatile pattern, for the Today page."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from forward.composition import get_forward_tracking_service
from forward.services.forward_tracking import ForwardTrackingService

router = APIRouter()


@router.get("/tracker/oversold-volatile")
def forward_tracker(
    service: ForwardTrackingService = Depends(get_forward_tracking_service),
) -> dict[str, Any]:
    """Running score of the pattern, graded only on weeks that had not happened when it was recorded."""
    return service.oversold_volatile_summary()


@router.get("/tracker/gap-plan")
def gap_plan_status(
    service: ForwardTrackingService = Depends(get_forward_tracking_service),
) -> dict[str, Any]:
    """The Monday gap plan: this week's call, live and replayed paper results, and open trades."""
    return service.gap_plan_summary()
