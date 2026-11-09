"""Point-in-time portfolio snapshots for learning records."""

from __future__ import annotations

from typing import Any

from forward.core.time import timestamp_sort_key
from forward.services.portfolio_models import BookPosition, _apply_fill, _clean


def snapshots_at_entry(
    entries: list[dict[str, Any]],
    fills: list[dict[str, Any]],
    capitals: dict[str, float],
    sector_of: Any,
) -> dict[Any, dict[str, Any] | None]:
    """The portfolio as each entry saw it, replayed from fills.

    ``entries`` carry ``key``, ``ts``, ``deployment_id`` and ``symbol``;
    ``fills`` are incremental fills with ``ts``. A single chronological sweep
    maintains average-cost lots per (deployment, symbol); each entry snapshots
    the state at its own timestamp — fills *at or before* the entry count, so
    the episode's own entry fill is included: this is exposure *upon* entry,
    stated as such.

    Point-in-time by construction: only the append-only log is read, so a
    rebuild years later replays the same book. Anything unresolvable (no
    timestamp, no deployment, no symbol) yields ``None`` fields, never zeros —
    missing stays missing.
    """
    ordered_fills = sorted(fills, key=lambda fill: timestamp_sort_key(fill.get("ts")))
    ordered_entries = sorted(
        [e for e in entries if e.get("ts") is not None],
        key=lambda entry: timestamp_sort_key(entry.get("ts")),
    )
    lots: dict[tuple[str, str], BookPosition] = {}
    snapshots: dict[Any, dict[str, Any] | None] = {}
    cursor = 0
    for entry in ordered_entries:
        key = entry.get("key")
        stamp = timestamp_sort_key(entry.get("ts"))
        while (
            cursor < len(ordered_fills)
            and timestamp_sort_key(ordered_fills[cursor].get("ts")) <= stamp
        ):
            fill = ordered_fills[cursor]
            cursor += 1
            dep = fill.get("deployment_id")
            symbol = _clean(fill.get("symbol"))
            if not dep or not symbol:
                continue
            lot = lots.setdefault(
                (str(dep), symbol),
                BookPosition(symbol=symbol, deployment_id=str(dep)),
            )
            _apply_fill(lot, fill)
        snapshots[key] = _snapshot_for(entry, lots, capitals, sector_of)
    for entry in entries:
        snapshots.setdefault(entry.get("key"), None)
    return snapshots


def _snapshot_for(
    entry: dict[str, Any],
    lots: dict[tuple[str, str], BookPosition],
    capitals: dict[str, float],
    sector_of: Any,
) -> dict[str, Any] | None:
    dep = entry.get("deployment_id")
    symbol = _clean(entry.get("symbol"))
    if not dep or not symbol:
        return {"exposure": None, "allocation": None, "sector_pct": None, "concentration": None}
    values: dict[str, float] = {}
    for (_dep, sym), position in lots.items():
        if position.qty != 0 and position.avg_price > 0:
            values[sym] = values.get(sym, 0.0) + abs(position.qty) * position.avg_price
    gross = sum(values.values())
    allocation = capitals.get(str(dep))
    sector = sector_of(symbol)
    sector_value = sum(v for s, v in values.items() if sector_of(s) == sector)
    return {
        "exposure": round(gross, 2),
        "allocation": allocation,
        "sector_pct": round(sector_value / gross, 4) if gross else None,
        "concentration": round(values.get(symbol, 0.0) / gross, 4) if gross else None,
    }
