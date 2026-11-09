"""Canonical identity for saved strategy definitions."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_definition(definition: dict[str, Any] | str) -> str:
    """Serialize equal strategy definitions to the same stable JSON string.

    Args:
        definition: Parsed strategy definition or its JSON representation.

    Returns:
        JSON with sorted keys and no extra whitespace.

    Raises:
        TypeError: If the decoded definition is not an object.
        ValueError: If the JSON contains values that cannot be serialized.
    """
    if isinstance(definition, str):
        definition = json.loads(definition)
    if not isinstance(definition, dict):
        raise TypeError("strategy definition must be an object")
    return json.dumps(
        definition,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def definition_hash(canonical: str) -> str:
    """Return the SHA-256 digest of a canonical strategy definition.

    Args:
        canonical: Canonical JSON returned by :func:`canonical_definition`.

    Returns:
        Lowercase hexadecimal SHA-256 digest.
    """
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = ["canonical_definition", "definition_hash"]
