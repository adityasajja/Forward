"""Market domain models."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from forward.core.instrument_models import Instrument


@dataclass(frozen=True, slots=True)
class Bar:
    """Observed OHLCV bar; volume is ``None`` when the feed omits it."""

    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None
    vwap: float | None = None
    open_interest: float | None = None

    @property
    def typical_price(self) -> float:
        return (self.high + self.low + self.close) / 3.0

    @property
    def true_range_forecast(self) -> float:
        return self.high - self.low


@dataclass(frozen=True, slots=True)
class Tick:
    ts: datetime
    last: float
    bid: float | None = None
    ask: float | None = None
    bid_size: float | None = None
    ask_size: float | None = None
    volume: float | None = None

    @property
    def mid(self) -> float | None:
        if self.bid is not None and self.ask is not None:
            return (self.bid + self.ask) / 2.0
        return None


@dataclass(frozen=True, slots=True)
class LiveMarketDepth:
    """One price level in a live market quote.

    Attributes:
        price: Price at this level.
        quantity: Available units at this level.
        order_count: Number of orders at this level.
        side: Feed value that identifies the order side.
    """

    price: float
    quantity: int
    order_count: int
    side: int


@dataclass(frozen=True, slots=True)
class LiveMarketQuote:
    """One normalized quote delivered by a live market-feed adapter.

    Attributes:
        last: Most recent traded price.
        quantity: Quantity in the most recent trade.
        volume: Total traded volume for the session.
        high: Session high.
        low: Session low.
        open: Session open.
        close: Previous session close.
        traded_at: Timestamp of the most recent trade.
        bid: Best bid price, when the feed supplies one.
        bid_quantity: Quantity at the best bid, when supplied.
        ask: Best ask price, when the feed supplies one.
        ask_quantity: Quantity at the best ask, when supplied.
        depth: Available market-depth levels.
    """

    last: float
    quantity: int
    volume: int
    high: float
    low: float
    open: float
    close: float
    traded_at: datetime
    bid: float | None = None
    bid_quantity: int | None = None
    ask: float | None = None
    ask_quantity: int | None = None
    depth: tuple[LiveMarketDepth, ...] = ()


@dataclass(frozen=True, slots=True)
class MarketQuote:
    """One broker's latest quote for a resolved instrument."""

    instrument: Instrument
    last: float
    volume: float | None = None


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    """All bars observed at a single timestamp, keyed by symbol."""

    ts: datetime
    bars: dict[str, Bar]

    def price(self, symbol: str) -> float | None:
        bar = self.bars.get(symbol)
        return bar.close if bar else None


@dataclass(frozen=True, slots=True)
class InstrumentReference:
    """A listed symbol with the broker ID needed to request market data.

    Attributes:
        symbol: Exact symbol shown to the operator.
        exchange: Segment that lists the symbol.
        contract_id: Broker ID for market data requests.
    """

    symbol: str
    exchange: str
    contract_id: str


@dataclass(frozen=True, slots=True)
class QuoteBatch:
    """Quote rows and an optional broker error message.

    Attributes:
        rows: Quote rows returned by the market source.
        error: Broker error text, if the request was rejected.
    """

    rows: list[dict[str, Any]]
    error: str | None = None


@dataclass(frozen=True, slots=True)
class BrokerReadResult:
    """Rows returned by one read-only broker account request.

    Attributes:
        rows: Broker records normalized into a list of dictionaries.
        error: Broker error text, if the request was rejected.
    """

    rows: list[dict[str, Any]]
    error: str | None = None


@dataclass(frozen=True, slots=True)
class MarketDataStatusSnapshot:
    """Stored market data facts used to report freshness.

    Attributes:
        stock_as_of: Latest date among the inspected stock cache files.
        index_as_of: Latest date in the NIFTY 50 cache.
        index_origin: Source label saved with the index cache.
        quality: Last history quality report, when available.
        sample_count: Number of stock cache files inspected.
        demo: Whether the data root has a demo marker.
    """

    stock_as_of: date | None
    index_as_of: date | None
    index_origin: str | None
    quality: Mapping[str, Any] | None
    sample_count: int
    demo: bool


@dataclass(frozen=True, slots=True)
class DailyClose:
    """One symbol's closing price for one market session.

    Attributes:
        session: ISO market date, or ``None`` when the cache has no date.
        close: Closing price for the session.
    """

    session: str | None
    close: float


@dataclass(frozen=True, slots=True)
class DashboardHistorySample:
    """The close data used to calculate dashboard market breadth.

    Attributes:
        universe_size: Number of symbols in the exchange history cache.
        symbols: A sorted sample of symbols and their recent daily closes.
    """

    universe_size: int
    symbols: Mapping[str, tuple[DailyClose, ...]]
