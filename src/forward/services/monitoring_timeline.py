"""Build the readable event timeline shown for a deployment.

This module turns order rows and their event rows into timeline entries. It has
no storage or runner dependencies, so the same event data always produces the
same projection.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

#: Timeline stages in causal order. The UI uses this order for its pipeline.
TIMELINE_STAGES = ("signal", "risk", "order", "fill", "position")

_STAGE_BY_STATUS = {
    "NEW": "order",
    "VALIDATING": "risk",
    "RISK_APPROVED": "risk",
    "SUBMITTED": "order",
    "ACKNOWLEDGED": "order",
    "PARTIALLY_FILLED": "fill",
    "FILLED": "fill",
    "CANCEL_PENDING": "order",
    "CANCELLED": "order",
    "REJECTED": "risk",
    "EXPIRED": "order",
}


@dataclass(frozen=True)
class TimelineEntry:
    """One step in the Signal → Risk → Order → Fill → Position chain."""

    ts: datetime
    stage: str
    deployment_id: str
    symbol: str | None
    summary: str
    outcome: str
    reason: str | None
    order_id: str | None
    side: str | None
    detail: dict[str, Any]


def loads_json(value: Any) -> Any:
    """Decode JSON values while leaving already-decoded values unchanged.

    Args:
        value: JSON text, bytes, or an already-decoded value.

    Returns:
        The decoded value, or ``None`` when the JSON text is invalid.
    """
    if isinstance(value, (str, bytes)):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return None
    return value


def as_datetime(value: Any) -> datetime | None:
    """Parse a datetime value and treat naive timestamps as UTC.

    Args:
        value: A datetime object or ISO formatted timestamp.

    Returns:
        A timezone-aware datetime, or ``None`` when the value is unsupported.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def timeline_from_log(
    deployment_id: str,
    rows: list[dict[str, Any]],
    by_order: dict[str, list[dict[str, Any]]],
) -> list[TimelineEntry]:
    """Project order rows and events into a causally ordered timeline.

    Args:
        deployment_id: Deployment that owns the rows.
        rows: Order rows for that deployment.
        by_order: Event rows grouped by order ID.

    Returns:
        Timeline entries ordered by timestamp, stage, and order ID.
    """
    entries: list[TimelineEntry] = []

    for row in rows:
        symbol = row.get("symbol")
        side = row.get("side")
        order_id = row["order_id"]

        for event in by_order.get(str(order_id), []):
            to_status = str(event.get("to_status") or "")
            stage = _STAGE_BY_STATUS.get(to_status)
            if stage is None:
                continue
            ts = as_datetime(event.get("ts")) or as_datetime(row.get("created_at"))
            if ts is None:
                continue
            raw = loads_json(event.get("raw")) or {}
            if not isinstance(raw, dict):
                raw = {}

            reason = event.get("reject_reason") or raw.get("reason")
            summary, outcome = _describe(
                to_status=to_status,
                symbol=symbol,
                side=side,
                row=row,
                event=event,
                raw=raw,
            )
            entries.append(
                TimelineEntry(
                    ts=ts,
                    stage=stage,
                    deployment_id=deployment_id,
                    symbol=symbol,
                    summary=summary,
                    outcome=outcome,
                    reason=reason,
                    order_id=order_id,
                    side=str(side) if side else None,
                    detail={
                        key: value
                        for key, value in {
                            "to_status": to_status,
                            "rule": raw.get("rule"),
                            "reason": raw.get("reason"),
                            "filled_qty": event.get("filled_qty"),
                            "filled_price": event.get("filled_price"),
                            "commission": event.get("commission"),
                            "slippage_bps": event.get("slippage_bps"),
                            "quantity": row.get("quantity"),
                            "strategy_version": row.get("strategy_version"),
                        }.items()
                        if value is not None
                    },
                )
            )

            if stage == "fill" and event.get("filled_qty"):
                entries.append(
                    TimelineEntry(
                        ts=ts,
                        stage="position",
                        deployment_id=deployment_id,
                        symbol=symbol,
                        summary=(
                            f"{symbol} position updated to "
                            f"{float(event['filled_qty']):g} from this order"
                        ),
                        outcome="recorded",
                        reason=None,
                        order_id=order_id,
                        side=str(side) if side else None,
                        detail={
                            key: value
                            for key, value in {
                                "filled_qty": event.get("filled_qty"),
                                "filled_price": event.get("filled_price"),
                                "side": side,
                            }.items()
                            if value is not None
                        },
                    )
                )

    entries.sort(key=lambda entry: (entry.ts, TIMELINE_STAGES.index(entry.stage), entry.order_id or ""))
    return entries


def _describe(
    *,
    to_status: str,
    symbol: Any,
    side: Any,
    row: dict[str, Any],
    event: dict[str, Any],
    raw: dict[str, Any],
) -> tuple[str, str]:
    """Format one event as a readable summary and outcome.

    Args:
        to_status: Status recorded by the event.
        symbol: Order instrument symbol.
        side: Order side.
        row: Source order row.
        event: Source event row.
        raw: Decoded event payload.

    Returns:
        A summary and an outcome label for the event.
    """
    name = str(symbol or "?")
    quantity = row.get("quantity")

    if to_status == "NEW":
        rule = raw.get("rule")
        reason = raw.get("reason")
        lead = f"{name} {side} signal"
        if rule:
            lead += f" from {rule}"
        if reason:
            return f"{lead} — {reason}", "observed"
        return lead, "observed"

    if to_status == "VALIDATING":
        return f"{name} sent to the risk gate", "observed"
    if to_status == "RISK_APPROVED":
        return f"{name} approved by risk", "approved"
    if to_status == "REJECTED":
        why = event.get("reject_reason") or raw.get("reason") or "no reason given"
        return f"{name} rejected — {why}", "rejected"
    if to_status == "SUBMITTED":
        return (
            f"{name} order sent ({quantity:g})" if quantity else f"{name} order sent",
            "placed",
        )
    if to_status == "ACKNOWLEDGED":
        return (
            f"{name} order placed ({quantity:g})" if quantity else f"{name} order placed",
            "placed",
        )
    if to_status == "PARTIALLY_FILLED":
        filled = event.get("filled_qty")
        return (
            f"{name} partially filled — {float(filled):g} so far",
            "filled" if filled is not None else "observed",
        )
    if to_status == "FILLED":
        filled = event.get("filled_qty")
        price = event.get("filled_price")
        if filled is not None and price is not None:
            return f"{name} filled {float(filled):g} @ {float(price):,.2f}", "filled"
        return f"{name} filled", "filled"
    if to_status in {"CANCELLED", "CANCEL_PENDING"}:
        return f"{name} order cancelled", "observed"
    if to_status == "EXPIRED":
        return f"{name} order expired unfilled", "observed"
    return f"{name} {to_status.lower().replace('_', ' ')}", "observed"
