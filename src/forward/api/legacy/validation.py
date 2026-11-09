"""Stored validation and evidence report routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

router = APIRouter()


@router.get("/validation")
def validation_report() -> dict[str, Any]:
    """Return the latest out-of-sample run for the research strategies.

    The report includes the random-selection control, so readers can compare
    each strategy with a result that does not use its signal.
    """
    from forward.wiring.research import get_validation_report_service

    return get_validation_report_service().paper_validation()


@router.get("/validation/episodic-pivot")
def episodic_pivot_report() -> dict[str, Any]:
    """Return the episodic-pivot evidence and out-of-sample validation."""
    from forward.wiring.research import get_validation_report_service

    return get_validation_report_service().episodic_pivot()


@router.get("/validation/alpha-hunt")
def alpha_hunt_report() -> dict[str, Any]:
    """Return registered alpha candidates and results for available universes."""
    from forward.wiring.research import get_validation_report_service

    return get_validation_report_service().alpha_hunt()


@router.get("/evidence")
def evidence_report() -> dict[str, Any]:
    """Return measured findings with the credibility count for each report."""
    from forward.wiring.research import get_validation_report_service

    return get_validation_report_service().evidence()
