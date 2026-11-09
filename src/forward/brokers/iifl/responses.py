"""Helpers for reading IIFL API response envelopes."""

from __future__ import annotations

from typing import Any

_EMPTY_STATES = ("no trade", "no holding", "no position", "no order", "no record")


def rows(payload: Any) -> list[dict[str, Any]]:
    """Return record rows from an IIFL response envelope.

    Args:
        payload: Raw response returned by an IIFL endpoint.

    Returns:
        Dictionary rows found in the response.
    """
    if empty_state(payload):
        return []
    if isinstance(payload, dict):
        if "result" not in payload:
            raise ValueError("IIFL response has no result field")
        value = payload["result"]
        if value is None:
            return []
        if isinstance(value, dict):
            return [value]
        if isinstance(value, list):
            if any(not isinstance(row, dict) for row in value):
                raise ValueError("IIFL response result contains a non-object row")
            return value
        raise ValueError("IIFL response result must be an object or row list")
    if isinstance(payload, list):
        if any(not isinstance(row, dict) for row in payload):
            raise ValueError("IIFL response contains a non-object row")
        return payload
    raise ValueError("IIFL response must be an object envelope or row list")


def empty_state(node: Any) -> bool:
    """Return whether an IIFL error row means a valid empty result.

    Args:
        node: Response row to inspect.

    Returns:
        Whether the row reports a known empty result.
    """
    if not isinstance(node, dict):
        return False
    message = str(node.get("message") or "").lower()
    return any(hint in message for hint in _EMPTY_STATES)


def broker_error(payload: Any) -> str | None:
    """Return the broker error message from a nested response, if present.

    Args:
        payload: Raw response returned by an IIFL endpoint.

    Returns:
        Error text, or ``None`` when the response has no error.
    """
    if not isinstance(payload, dict):
        return None

    def failure(node: Any) -> str | None:
        if not isinstance(node, dict):
            return None
        status = node.get("status")
        if isinstance(status, str) and status.strip().lower() not in ("ok", "success"):
            message = str(node.get("message") or status).strip()
            if empty_state(node):
                return None
            return message
        return None

    outer = failure(payload)
    if outer:
        return outer

    result = payload.get("result")
    nested_rows = result if isinstance(result, list) else [result]
    for row in nested_rows:
        inner = failure(row)
        if inner:
            return inner
    return None
