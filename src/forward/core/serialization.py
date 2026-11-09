"""Convert common Python values into JSON-safe data."""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any


def json_safe(value: Any) -> Any:
    """Convert nested values into data accepted by JSON encoders.

    Args:
        value: Value or collection to convert.

    Returns:
        A JSON-safe value with non-finite numbers represented as ``None``.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if hasattr(value, "item"):
        return json_safe(value.item())
    return value
