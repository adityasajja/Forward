"""Shared terminal output helpers for Forward commands."""

from __future__ import annotations


def print_table(rows: list[dict], limit: int) -> None:
    """Print rows, skipping nested columns that would render unreadably.

    Args:
        rows: Rows to display.
        limit: Maximum number of rows to print.
    """
    import pandas as pd

    frame = pd.DataFrame(rows)
    if frame.empty:
        print("  (empty)")
        return
    flat = [
        column
        for column in frame.columns
        if not frame[column].map(lambda value: isinstance(value, (dict, list))).any()
    ]
    print(frame[flat].head(limit).to_string(index=False))
    if len(frame) > limit:
        print(f"  ... {len(frame) - limit} more (raise with --limit)")
