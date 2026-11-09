"""Shared SQL repository helpers."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any
from uuid import uuid4


def json_text(value: dict[str, Any] | str | None) -> str | None:
    """Serialize a JSON mapping or keep an already serialized string.

    Args:
        value: JSON-compatible mapping, serialized JSON, or ``None``.

    Returns:
        Serialized JSON text, or ``None`` when no value was given.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, default=str, sort_keys=True)


def new_id() -> str:
    """Return a random hexadecimal identifier.

    Returns:
        A 32-character identifier.
    """
    return uuid4().hex


def one(row: Any | None) -> dict[str, Any] | None:
    """Convert one SQLAlchemy row to a dictionary.

    Args:
        row: SQLAlchemy result row, or ``None`` when there is no match.

    Returns:
        The row values, or ``None`` when no row was found.
    """
    return None if row is None else dict(row._mapping)


def many(rows: Iterable[Any]) -> list[dict[str, Any]]:
    """Convert SQLAlchemy result rows to dictionaries.

    Args:
        rows: SQLAlchemy rows in the order they should be returned.

    Returns:
        The row values as dictionaries.
    """
    return [dict(row._mapping) for row in rows]


def decode_json_fields(row: Mapping[str, Any], fields: Iterable[str]) -> dict[str, Any]:
    """Copy a row and decode serialized JSON values in selected fields.

    Args:
        row: Mapping returned by a repository query.
        fields: Field names whose string values contain JSON text.

    Returns:
        A new dictionary with the selected JSON strings decoded.
    """
    decoded = dict(row)
    for field in fields:
        value = decoded.get(field)
        if isinstance(value, str):
            decoded[field] = json.loads(value)
    return decoded
