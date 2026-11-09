"""Build the forward-evidence count summary from learning rows."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from forward.core.session import IST


def summarize_forward_evidence(
    rows: list[dict[str, Any]],
    *,
    strategy_id: str | None,
    strategy_version: int | None,
    as_of: datetime,
) -> dict[str, Any]:
    """Count forward evidence by class, time, strategy, and version.

    Args:
        rows: Learning dataset rows to summarize.
        strategy_id: Optional strategy filter.
        strategy_version: Optional strategy-version filter.
        as_of: Current exchange-local time for the response and date windows.

    Returns:
        Counts for the selected rows, grouped by evidence class and strategy.
    """
    today_date = as_of.date()
    cutoff_7d = as_of - timedelta(days=7)

    total_genuine_forward = 0
    today_genuine_forward = 0
    last_7d_genuine_forward = 0

    by_class: dict[str, dict[str, int]] = {
        "IN_SAMPLE": {"total": 0, "today": 0, "last_7d": 0},
        "PAPER_FORWARD": {"total": 0, "today": 0, "last_7d": 0},
        "LIVE_FORWARD": {"total": 0, "today": 0, "last_7d": 0},
    }

    strat_buckets: dict[tuple[str, int | None], dict[str, Any]] = {}

    for row in rows:
        row_strat = str(row.get("strategy_id") or row.get("strategy_key") or "UNKNOWN")
        row_ver = row.get("strategy_version")
        if row_ver is not None:
            try:
                row_ver = int(row_ver)
            except (ValueError, TypeError):
                row_ver = None

        if strategy_id and row_strat != strategy_id:
            continue
        if strategy_version is not None and row_ver != strategy_version:
            continue

        raw_class = str(row.get("evidence_class") or "").upper()
        if raw_class == "PAPER_FORWARD":
            class_key = "PAPER_FORWARD"
        elif raw_class == "LIVE_FORWARD":
            class_key = "LIVE_FORWARD"
        else:
            class_key = "IN_SAMPLE"

        is_forward = class_key in ("PAPER_FORWARD", "LIVE_FORWARD")

        timestamp_value = row.get("exit_ts") or row.get("entry_ts") or row.get("created_at")
        row_datetime: datetime | None = None
        if isinstance(timestamp_value, datetime):
            row_datetime = (
                timestamp_value
                if timestamp_value.tzinfo
                else timestamp_value.replace(tzinfo=UTC)
            )
        elif isinstance(timestamp_value, str):
            try:
                row_datetime = datetime.fromisoformat(timestamp_value.replace("Z", "+00:00"))
                if not row_datetime.tzinfo:
                    row_datetime = row_datetime.replace(tzinfo=UTC)
            except ValueError:
                row_datetime = None

        is_today = False
        is_7d = False
        if row_datetime is not None:
            row_ist = row_datetime.astimezone(IST)
            is_today = row_ist.date() == today_date
            is_7d = row_ist >= cutoff_7d

        by_class[class_key]["total"] += 1
        if is_today:
            by_class[class_key]["today"] += 1
        if is_7d:
            by_class[class_key]["last_7d"] += 1

        if is_forward:
            total_genuine_forward += 1
            if is_today:
                today_genuine_forward += 1
            if is_7d:
                last_7d_genuine_forward += 1

        strategy_key = (row_strat, row_ver)
        if strategy_key not in strat_buckets:
            strat_buckets[strategy_key] = {
                "strategy_id": row_strat,
                "strategy_version": row_ver,
                "total_genuine_forward": 0,
                "today_genuine_forward": 0,
                "last_7d_genuine_forward": 0,
                "paper_forward": 0,
                "live_forward": 0,
                "in_sample": 0,
                "total": 0,
            }
        bucket = strat_buckets[strategy_key]
        bucket["total"] += 1
        if class_key == "PAPER_FORWARD":
            bucket["paper_forward"] += 1
        elif class_key == "LIVE_FORWARD":
            bucket["live_forward"] += 1
        else:
            bucket["in_sample"] += 1

        if is_forward:
            bucket["total_genuine_forward"] += 1
            if is_today:
                bucket["today_genuine_forward"] += 1
            if is_7d:
                bucket["last_7d_genuine_forward"] += 1

    by_strategy = sorted(
        strat_buckets.values(),
        key=lambda bucket: (bucket["total_genuine_forward"], bucket["total"]),
        reverse=True,
    )

    return {
        "total_genuine_forward": total_genuine_forward,
        "today_genuine_forward": today_genuine_forward,
        "last_7d_genuine_forward": last_7d_genuine_forward,
        "by_class": by_class,
        "by_strategy": by_strategy,
        "filtered_strategy_id": strategy_id,
        "filtered_strategy_version": strategy_version,
        "as_of": as_of.isoformat(),
    }
