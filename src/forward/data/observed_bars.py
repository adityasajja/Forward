"""Build bounded five-minute bars from observed market quotes.

These bars are sampled from live quotes. They are not exchange-provided OHLC
bars. The caller must hold its feed lock while adding quotes or taking a
snapshot.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from forward.core.observed_bars_ports import ObservedBarsPort
from forward.core.session import IST

_BAR_MINUTES = 5
_BAR_SECONDS = _BAR_MINUTES * 60
_SESSION_START = "09:15"
_SESSION_END = "15:30"
_MAX_QUOTE_GAP_SECONDS = 15
_MAX_RECEIPT_LAG_SECONDS = 2
_BAR_START_GRACE_SECONDS = 5
_BAR_END_GRACE_SECONDS = 5


class ObservedBars(ObservedBarsPort):
    """Collect quote-derived bars for the current session."""

    def __init__(self) -> None:
        """Create an empty collector for one market-data feed."""
        self.day: str | None = None
        self.bars: dict[str, dict[str, dict[str, Any]]] = {}
        self.last: dict[str, tuple[float, float]] = {}

    def add(
        self,
        symbol: str,
        price: float,
        volume: float,
        observed_at: float,
        received_at: float,
        *,
        index: bool = False,
    ) -> None:
        """Add one quote when it belongs to the current regular session.

        Args:
            symbol: Market-data symbol that produced the quote.
            price: Last traded price from the quote.
            volume: Cumulative traded volume from the quote.
            observed_at: Exchange timestamp as Unix seconds.
            received_at: Local receipt timestamp as Unix seconds.
            index: Whether the quote is for an index without traded volume.
        """
        observed_time = datetime.fromtimestamp(observed_at, IST)
        received_time = datetime.fromtimestamp(received_at, IST)
        observed_clock = observed_time.strftime("%H:%M")
        if (
            observed_time.date() != received_time.date()
            or not _SESSION_START <= observed_clock < _SESSION_END
        ):
            return
        if not all(math.isfinite(value) for value in (price, volume)):
            return
        if price <= 0 or volume < 0:
            return

        day = observed_time.date().isoformat()
        if self.day != day:
            self.day = day
            self.bars = {}
            self.last = {}

        start = observed_time.replace(
            minute=observed_time.minute // _BAR_MINUTES * _BAR_MINUTES,
            second=0,
            microsecond=0,
        )
        bar_epoch = start.timestamp()
        previous = self.last.get(symbol)
        if previous is not None and observed_at < previous[0]:
            return

        symbol_bars = self.bars.setdefault(symbol, {})
        bar_key = start.isoformat()
        if bar_key not in symbol_bars:
            symbol_bars[bar_key] = {
                "ts": bar_key,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 0.0,
                "last": observed_at,
                "valid": observed_at - bar_epoch <= _BAR_START_GRACE_SECONDS,
            }

        bar = symbol_bars[bar_key]
        gap = observed_at - previous[0] if previous is not None else 0
        delta = (
            volume - previous[1]
            if previous is not None
            else volume if observed_clock == _SESSION_START else 0
        )
        receipt_lag = received_at - observed_at
        if (
            gap > _MAX_QUOTE_GAP_SECONDS
            or delta < 0
            or not 0 <= receipt_lag <= _MAX_RECEIPT_LAG_SECONDS
        ):
            bar["valid"] = False

        bar.update(
            high=max(bar["high"], price),
            low=min(bar["low"], price),
            close=price,
            last=observed_at,
        )
        bar["volume"] += 0.0 if index else max(0.0, delta)
        self.last[symbol] = (observed_at, volume)

    def snapshot(self, now: datetime) -> dict[str, Any]:
        """Return completed, valid bars observed before ``now``.

        Args:
            now: Current exchange-local time used to close the active interval.

        Returns:
            The current session date, source label, and valid completed bars.
        """
        now_epoch = now.timestamp()
        bars: dict[str, list[dict[str, Any]]] = {}
        for symbol, symbol_bars in self.bars.items():
            completed = []
            for bar in symbol_bars.values():
                bar_start = datetime.fromisoformat(bar["ts"]).timestamp()
                bar_end = bar_start + _BAR_SECONDS
                if (
                    bar_end <= now_epoch
                    and bar["valid"]
                    and bar["last"] >= bar_end - _BAR_END_GRACE_SECONDS
                ):
                    completed.append(dict(bar))
            bars[symbol] = completed
        return {"day": self.day, "source": "observed_live_quotes", "bars": bars}
