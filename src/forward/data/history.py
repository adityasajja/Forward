"""Read daily bars from the configured local parquet cache.

One parquet per symbol under the configured data root. Re-runs are cheap
because the panel cache reads all symbol files together.
"""

from __future__ import annotations

from collections.abc import Iterable

import pandas as pd

from forward.config.settings import research_data_root

DAILY_DIR = "iifl_daily"
CACHE_ROOT = research_data_root() / DAILY_DIR


def load_cached(
    exchange: str = "NSEEQ", symbols: Iterable[str] | None = None
) -> dict[str, pd.DataFrame]:
    """All cached frames for an exchange: {symbol: df}.

    Pass ``symbols`` to read only those files. Reads via Polars (multi-threaded
    Rust Arrow deserialization, ~2.5x faster than pandas' own parquet reader),
    then hands back pandas — every consumer of this cache is still pandas-based.
    """
    from forward.data import panel

    outdir = CACHE_ROOT / exchange.upper()
    # One columnar file, current with the per-symbol ones (see forward.data.panel):
    # ~0.1s instead of ~5s for the whole exchange. Copies, because callers here
    # have always been free to modify what they are handed.
    shared = panel.frames(outdir)
    if symbols is None:
        return {s: df.copy() for s, df in shared.items()}
    return {s.upper(): shared[s.upper()].copy() for s in symbols if s.upper() in shared}
