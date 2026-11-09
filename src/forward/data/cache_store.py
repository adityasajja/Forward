"""Read daily bars from the application's consolidated local cache."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pandas as pd

from forward.data.panel import frames


class PanelDailyBarCache:
    """Load selected symbol frames through the shared parquet panel."""

    def load(self, directory: Path, filenames: Sequence[str]) -> dict[str, pd.DataFrame]:
        """Return cached frames keyed by their parquet file stems.

        Args:
            directory: Directory containing the per-symbol parquet files.
            filenames: File stems requested by the caller.

        Returns:
            A mapping containing only requested frames that are present.
        """
        cached = frames(directory)
        return {name: cached[name] for name in filenames if name in cached}

    def load_file(self, path: Path) -> pd.DataFrame | None:
        """Return one cached frame by its parquet path.

        Args:
            path: Full path to the symbol's parquet file.

        Returns:
            The daily frame, or ``None`` when it is not cached.
        """
        return frames(path.parent).get(path.stem)
