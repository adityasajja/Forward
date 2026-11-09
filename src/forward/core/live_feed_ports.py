"""Ports for shared live market-feed connections."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from forward.core.instrument_models import Instrument
from forward.core.market_models import LiveMarketQuote, MarketSnapshot, Tick

FeedListener = Callable[[str, LiveMarketQuote], None]
IndexListener = Callable[[str, LiveMarketQuote], None]
FeedErrorListener = Callable[[int, str], None]


class LiveFeedConnectionPort(Protocol):
    """Subscribe to live market events from a shared feed connection."""

    @property
    def is_connected(self) -> bool:
        """Return whether the shared feed connection is online."""

    def subscribe_feed(self, topics: list[str]) -> None:
        """Subscribe to instrument quote topics."""

    def unsubscribe_feed(self, topics: list[str]) -> None:
        """Stop receiving instrument quote topics."""

    def subscribe_index(self, topics: list[str]) -> None:
        """Subscribe to index quote topics."""

    def subscribe_open_interest(self, topics: list[str]) -> None:
        """Subscribe to open-interest topics for derivative instruments."""

    def add_feed_listener(self, listener: FeedListener) -> None:
        """Register a listener for instrument quote events."""

    def remove_feed_listener(self, listener: FeedListener) -> None:
        """Remove a listener for instrument quote events."""

    def add_index_listener(self, listener: IndexListener) -> None:
        """Register a listener for index quote events."""

    def remove_index_listener(self, listener: IndexListener) -> None:
        """Remove a listener for index quote events."""


class LiveMarketFeedPort(LiveFeedConnectionPort, Protocol):
    """Resolve instruments and manage the process-wide live feed connection."""

    def ensure_connected(
        self,
        *,
        on_feed: FeedListener,
        on_index: IndexListener,
        on_error: FeedErrorListener,
        feed_topics: list[str],
        index_topics: list[str],
    ) -> bool:
        """Connect when needed and restore active subscriptions on a new client.

        Args:
            on_feed: Receives normalized instrument quotes.
            on_index: Receives normalized index quotes.
            on_error: Receives broker connection errors.
            feed_topics: Instrument topics to restore after connection.
            index_topics: Index topics to restore after connection.

        Returns:
            Whether the live feed is ready.
        """

    def resolve_contract_id(self, symbol: str, exchange: str) -> int | str:
        """Resolve one exact symbol and exchange to its contract identifier.

        Args:
            symbol: Exact trading symbol.
            exchange: Exchange segment for the symbol.

        Returns:
            Contract identifier used to build the feed topic.
        """


class LiveRunnerFeedPort(Protocol):
    """Supply live snapshots and quotes to a strategy runner."""

    def start(self) -> None:
        """Start the market feed."""

    def get(self, timeout: float) -> MarketSnapshot | None:
        """Return the next completed market snapshot, if one is ready."""

    def last_tick(self, symbol: str) -> Tick | None:
        """Return the most recent quote for one symbol."""

    def subscribe_instruments(self, instruments: list[Instrument]) -> None:
        """Subscribe to newly selected instruments."""

    def stop(self) -> None:
        """Stop the market feed."""


class LiveWarmupHistoryPort(Protocol):
    """Load historical bars before a live strategy starts."""

    def fetch(self, instruments: dict[str, Instrument], timeframe: str, bars: int) -> Any:
        """Return historical OHLCV rows for the requested instruments.

        Args:
            instruments: Contracts whose indicator history is required.
            timeframe: Bar interval used by the live feed.
            bars: Maximum number of completed bars per instrument.

        Returns:
            A pandas DataFrame with ``ts``, ``symbol``, and OHLCV columns.
        """
