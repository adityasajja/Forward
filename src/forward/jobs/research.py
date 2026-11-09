"""Scheduled jobs for daily research workflows."""

from __future__ import annotations

from pathlib import Path

from forward.core.historical_market_data_ports import CachedMarketFramesPort
from forward.jobs.daily import DailyJob
from forward.research.forward_tracker import run_daily as run_forward_tracker
from forward.research.gap_plan import run_daily as run_gap_plan


def build_research_jobs(
    data_root: Path, history: CachedMarketFramesPort
) -> list[DailyJob]:
    """Build the recurring jobs that update daily research records.

    Args:
        data_root: Directory that stores the research records.
        history: Port that reads cached daily market frames.

    Returns:
        The forward-tracker and gap-plan jobs, in run order.
    """
    # Both jobs run after the end-of-day history refresh.
    return [
        DailyJob(
            "forward_tracker",
            lambda: run_forward_tracker(data_root, history),
            at=(17, 30),
            weekdays_only=True,
        ),
        DailyJob(
            "gap_plan",
            lambda: run_gap_plan(data_root, history),
            at=(17, 45),
            weekdays_only=True,
        ),
    ]
