"""Build validated identity fields from a journal episode."""

from __future__ import annotations

from typing import Any


def episode_identity(episode: dict[str, Any]) -> tuple[str, str]:
    """Read the required symbol and side from one journal episode.

    Args:
        episode: Closed trade row returned by journal storage.

    Returns:
        The upper-case symbol and BUY or SELL side.

    Raises:
        KeyError: If the journal row has no symbol or side.
        ValueError: If the symbol is blank or the side is not supported.
    """
    raw_symbol = episode["symbol"]
    if raw_symbol is None:
        raise ValueError("journal episode symbol must not be null")
    symbol = str(raw_symbol).strip().upper()
    side = str(episode["side"]).strip().upper()
    if not symbol:
        raise ValueError("journal episode symbol must not be blank")
    if side not in {"BUY", "SELL"}:
        raise ValueError(f"journal episode side must be BUY or SELL, got {side!r}")
    return symbol, side
