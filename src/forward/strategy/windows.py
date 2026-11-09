"""Rolling bar history used by strategy contexts."""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from forward.core.market_models import Bar


@dataclass
class RollingWindow:
    """Keep the newest bars needed by one strategy.

    Args:
        size: Maximum number of bars to keep.
    """

    size: int = 500
    _items: list[Bar] = field(default_factory=list, init=False)

    def push(self, bar: Bar) -> None:
        """Add a bar and drop the oldest bar when the window is full.

        Args:
            bar: Completed market bar to add.
        """
        self._items.append(bar)
        if len(self._items) > self.size:
            del self._items[0]

    def closes(self) -> list[float]:
        """Return close prices in time order.

        Returns:
            Close prices for the bars in the window.
        """
        return [bar.close for bar in self._items]

    def frame(self) -> pd.DataFrame:
        """Return the bars as a dataframe.

        Returns:
            A dataframe with timestamp, OHLC, and volume columns.
        """
        return pd.DataFrame(
            [
                {
                    "ts": bar.ts,
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                }
                for bar in self._items
            ]
        )

    def __len__(self) -> int:
        """Return the number of bars currently stored."""
        return len(self._items)
