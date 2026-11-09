"""Price sources used by paper trading."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from math import isfinite

from forward.core.historical_market_data_ports import CachedInstrumentHistoryPort
from forward.core.live_market_data_ports import LiveTickPricePort
from forward.core.paper_models import TickPrice
from forward.core.time import utcnow


def _is_positive_finite(value: float | None) -> bool:
    """Return whether a market price is finite and above zero.

    Args:
        value: Price value read from the market feed.

    Returns:
        Whether the value can be used as a market price.
    """
    return value is not None and isfinite(value) and value > 0


def cached_adv_source(
    instruments: CachedInstrumentHistoryPort, *, days: int
) -> Callable[[str, str], float | None]:
    """Build an average-volume source from the cached instrument history.

    Args:
        instruments: Port that resolves listed symbols and reads their daily bars.
        days: Number of recent sessions used for the average.

    Returns:
        A callable that returns average daily volume, or ``None`` when it is
        unavailable.
    """

    def source(symbol: str, exchange: str) -> float | None:
        """Read recent average daily volume for one listed instrument.

        Args:
            symbol: Trading symbol to read.
            exchange: Exchange segment that lists the symbol.

        Returns:
            Average daily volume, or ``None`` when no usable data exists.
        """
        record = instruments.get(symbol)
        if record is None or record.exchange.upper() != exchange.upper() or not record.cache_file:
            return None
        frame = instruments.daily_bars(record.cache_file)
        if frame is None or frame.empty or "volume" not in frame.columns:
            return None
        volume = frame["volume"].tail(days)
        value = float(volume.mean())
        return value if value > 0 else None

    return source


def fixed_price_source(prices: dict[str, float]) -> Callable[[str, str], float | None]:
    """Build a fixed price source for tests or a known market session.

    Args:
        prices: Prices keyed by trading symbol.

    Returns:
        A callable that returns the selected symbol's price.
    """
    table = {k.upper(): float(v) for k, v in prices.items()}
    return lambda symbol, exchange: table.get(symbol.upper())  # noqa: ARG005


def live_tick_source(
    broadcaster: LiveTickPricePort,
    *,
    clock: Callable[[], datetime] | None = None,
) -> Callable[[str, str], TickPrice | None]:
    """Build a price source from the live tick stream.

    Args:
        broadcaster: Live market feed that supplies price and quote details.
        clock: Clock used to measure quote age.

    Returns:
        A callable that returns a :class:`TickPrice` for the requested symbol.
    """
    now_fn = clock or utcnow

    def source(symbol: str, exchange: str) -> TickPrice | None:  # noqa: ARG001
        """Read one live tick and calculate its age."""
        price, ts = broadcaster.latest_price_and_time(symbol)
        if not _is_positive_finite(price):
            return None

        now = now_fn()
        age = TickPrice(price=float(price), timestamp=ts).get_age_seconds(now)

        tick = broadcaster.latest_tick(symbol)
        open_px = (
            float(tick.open) if tick is not None and _is_positive_finite(tick.open) else None
        )
        bid = ask = None
        if (
            tick is not None
            and _is_positive_finite(tick.best_bid)
            and _is_positive_finite(tick.best_ask)
            and 0 < tick.best_bid <= tick.best_ask
        ):
            bid, ask = float(tick.best_bid), float(tick.best_ask)
        return TickPrice(
            price=float(price), timestamp=ts, age_seconds=age, open=open_px, bid=bid, ask=ask
        )

    return source
