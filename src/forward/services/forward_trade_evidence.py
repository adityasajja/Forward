"""Rules for selecting trades that count as forward evidence."""

from __future__ import annotations

from typing import Any

from forward.core.learning_evidence import (
    CLASS_LIVE_FORWARD,
    CLASS_PAPER_FORWARD,
)


def matches_strategy_version(row: dict[str, Any], version: int | None) -> bool:
    """Check the journal version using the shared evidence rule.

    Args:
        row: Forward journal row to check.
        version: Requested version, or ``None`` to include every version.

    Returns:
        Whether the row belongs to the requested version.
    """
    if version is None:
        return True

    row_version = row.get("strategy_version")
    if row_version is None or row_version == version:
        return True

    try:
        return int(row_version) == int(version)
    except (TypeError, ValueError):
        return False


def is_forward_trade(row: dict[str, Any]) -> bool:
    """Check whether the journal row is labeled as forward evidence.

    Args:
        row: Forward journal row to check.

    Returns:
        Whether the row is marked as paper or live forward evidence.
    """
    evidence_class = str(row.get("evidence_class") or "").upper()
    evidence_grade = str(row.get("evidence_grade") or "").lower()
    return evidence_class in (CLASS_PAPER_FORWARD, CLASS_LIVE_FORWARD) or (
        evidence_grade == "forward"
    )
