"""Shared rules for normalizing trading symbols."""

from __future__ import annotations

import re

_SERIES_SUFFIXES = frozenset(
    {
        "EQ",
        "BE",
        "BZ",
        "BL",
        "BT",
        "GC",
        "IL",
        "IQ",
        "IV",
        "NC",
        "ND",
        "SM",
        "ST",
        "RR",
    }
)
_WEEKLY_SERIES_SUFFIX = re.compile(r"^(N[1-9]|W[1-3])$")


def is_exchange_series_suffix(value: str) -> bool:
    """Return whether a suffix identifies a supported exchange series.

    Args:
        value: Suffix following a symbol's final hyphen.

    Returns:
        Whether the suffix is a supported series code.
    """
    return value in _SERIES_SUFFIXES or _WEEKLY_SERIES_SUFFIX.fullmatch(value) is not None


def canonical_symbol(symbol: str) -> str:
    """Reduce a symbol spelling to its bare uppercase ticker.

    Args:
        symbol: Symbol with optional whitespace or a recognized series suffix.

    Returns:
        The uppercase ticker without spaces or a recognized series suffix.
    """
    if not symbol:
        return ""
    text = str(symbol).strip().upper().replace(" ", "")
    base, separator, suffix = text.rpartition("-")
    if separator and base and is_exchange_series_suffix(suffix):
        return base
    return text
