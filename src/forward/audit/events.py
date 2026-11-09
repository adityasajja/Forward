"""Build the shared record shape used by both audit sinks."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any


def build_audit_event(
    *,
    action: str,
    actor: str | None = None,
    subject: str | None = None,
    detail: Any = None,
    result: str = "success",
    user_id: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    request_id: str | None = None,
    ip: str | None = None,
    ts: datetime | None = None,
) -> dict[str, Any]:
    """Create one JSON-ready audit event.

    Args:
        action: Stable name of the action.
        actor: Account or service that performed the action.
        subject: Object changed by the action.
        detail: Extra event details.
        result: Outcome label for the action.
        user_id: Account that owns the action.
        target_type: Kind of object changed.
        target_id: Identifier of the object changed.
        request_id: Request that caused the action.
        ip: Client address, when available.
        ts: Event time, or the current UTC time.

    Returns:
        A normalized event suitable for JSONL or database storage.

        Raises:
            TypeError: If ``detail`` contains a value JSON cannot encode.
            ValueError: If ``detail`` contains a non-finite number.
    """
    moment = ts or datetime.now(UTC)
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC).replace(tzinfo=None)
    return {
        "ts": moment.isoformat(timespec="seconds") + "Z",
        "actor": actor or "system",
        "action": action,
        "subject": subject or "",
        "detail": detail if isinstance(detail, str) else json.dumps(detail, allow_nan=False),
        "result": result,
        "user_id": user_id,
        "target_type": target_type,
        "target_id": target_id,
        "request_id": request_id,
        "ip": ip,
    }
