"""Value types and ports used by the paper execution workflow."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class TickPrice:
    """A live price with the time and quote details reported by its feed.

    Attributes:
        price: Last traded price.
        timestamp: Time when the feed observed the price.
        age_seconds: Age reported by the feed, when available.
        open: Current session open, when available.
        bid: Best bid from the same quote, when available.
        ask: Best ask from the same quote, when available.
    """

    price: float
    timestamp: datetime | None = None
    age_seconds: float | None = None
    open: float | None = None
    bid: float | None = None
    ask: float | None = None

    def get_age_seconds(self, now: datetime) -> float | None:
        """Return the quote age, or ``None`` when the feed supplied no time.

        Args:
            now: Current time used when the tick has a timestamp.

        Returns:
            Age in seconds, or ``None`` when neither age nor timestamp exists.
        """
        if self.age_seconds is not None:
            if not math.isfinite(self.age_seconds) or self.age_seconds < 0:
                raise ValueError("quote age must be finite and non-negative")
            return self.age_seconds
        if self.timestamp is None:
            return None

        now_dt = now
        tick_time = self.timestamp
        if tick_time.tzinfo is None and now_dt.tzinfo is not None:
            tick_time = tick_time.replace(tzinfo=now_dt.tzinfo)
        elif tick_time.tzinfo is not None and now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=tick_time.tzinfo)
        age_seconds = (now_dt - tick_time).total_seconds()
        if age_seconds < 0:
            raise ValueError("quote timestamp is in the future")
        return age_seconds


class PaperPriceSourcePort(Protocol):
    """Read one instrument's latest paper-execution price."""

    def __call__(self, symbol: str, exchange: str) -> float | TickPrice | None:
        """Return the latest price measurement for one listed instrument.

        Args:
            symbol: Exact trading symbol.
            exchange: Listing exchange segment.

        Returns:
            The latest price, or ``None`` when no price is available.
        """
