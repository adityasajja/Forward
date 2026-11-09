"""Provide dashboard queries for saved forward-research results."""

from __future__ import annotations

from typing import Any

from forward.core.forward_tracking_ports import (
    ForwardTrackingPort,
)
from forward.research import forward_tracker, gap_plan


class ForwardTrackingService:
    """Serve forward-tracking summaries through a storage port."""

    def __init__(self, tracking: ForwardTrackingPort) -> None:
        """Create the tracking query service.

        Args:
            tracking: Port that reads saved research state.
        """
        self._tracking = tracking

    def oversold_volatile_summary(self) -> dict[str, Any]:
        """Return the latest oversold-and-volatile forward report.

        Returns:
            The saved report and current evidence summary.
        """
        state = self._tracking.oversold_volatile_state()
        return forward_tracker.summary(state)

    def gap_plan_summary(self) -> dict[str, Any]:
        """Return the latest gap-plan report.

        Returns:
            The saved plan, replay results, and open trades.
        """
        state = self._tracking.gap_plan_state()
        return gap_plan.summary(state)
