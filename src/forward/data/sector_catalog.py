"""Read the symbol-to-industry catalog from the saved index files."""

from __future__ import annotations

import csv
from pathlib import Path

from forward.core.symbols import canonical_symbol


def read_sector_table(data_root: Path) -> dict[str, str]:
    """Read the symbol-to-industry map from index CSV files.

    Args:
        data_root: Directory that contains the universe directory.

    Returns:
        Industry names keyed by canonical symbol.
    """
    universe_dir = data_root / "universe"
    table: dict[str, str] = {}
    if not universe_dir.is_dir():
        return table
    for csv_path in sorted(universe_dir.glob("ind_*list.csv")):
        try:
            with csv_path.open("r", encoding="utf-8", errors="replace") as handle:
                for row in csv.DictReader(handle):
                    symbol = canonical_symbol(row.get("Symbol") or "")
                    industry = (row.get("Industry") or "").strip()
                    if symbol and industry:
                        table.setdefault(symbol, industry)
        except Exception:  # noqa: BLE001 — one damaged file must not hide others
            continue
    return table
