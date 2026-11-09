"""Load explicit instrument metadata for local market-data feeds."""

from __future__ import annotations

import json
from pathlib import Path

from forward.core.instrument_models import Instrument

_REQUIRED_FIELDS = frozenset(
    {
        "symbol",
        "asset_class",
        "currency",
        "exchange",
        "multiplier",
        "tick_size",
        "min_quantity",
        "quantity_step",
    }
)


def load_instruments(path: str | Path) -> dict[str, Instrument]:
    """Load a JSON array of complete instrument records.

    Args:
        path: JSON file containing one complete instrument object per row.

    Returns:
        Instrument records keyed by their canonical symbol.

    Raises:
        TypeError: If the file does not contain an array of objects.
        ValueError: If a row is incomplete, has unknown fields, or repeats a symbol.
    """
    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise TypeError(f"{source}: instrument catalog must be a JSON array")

    instruments: dict[str, Instrument] = {}
    allowed_fields = set(Instrument.model_fields)
    for index, row in enumerate(payload):
        if not isinstance(row, dict):
            raise TypeError(f"{source}: instrument row {index} must be an object")
        missing = _REQUIRED_FIELDS - set(row)
        unknown = set(row) - allowed_fields
        if missing or unknown:
            raise ValueError(
                f"{source}: instrument row {index} has missing fields "
                f"{sorted(missing)} and unknown fields {sorted(unknown)}"
            )
        instrument = Instrument.model_validate(row)
        if instrument.symbol in instruments:
            raise ValueError(
                f"{source}: duplicate instrument symbol {instrument.symbol!r}"
            )
        instruments[instrument.symbol] = instrument
    if not instruments:
        raise ValueError(f"{source}: instrument catalog is empty")
    return instruments
