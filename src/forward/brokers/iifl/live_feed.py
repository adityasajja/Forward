"""IIFL adapter for the shared live market-feed connection."""

from __future__ import annotations

import threading
import time

from loguru import logger

from forward.brokers.iifl.auth import SessionStore
from forward.brokers.iifl.bridge import BridgeClient
from forward.brokers.iifl.codec import IndexTick, MarketFeed
from forward.core.broker_catalog_ports import BrokerInstrumentCatalogPort
from forward.core.live_feed_ports import (
    FeedErrorListener,
    FeedListener,
    IndexListener,
)
from forward.core.market_models import LiveMarketDepth, LiveMarketQuote


def normalize_iifl_market_quote(packet: MarketFeed) -> LiveMarketQuote:
    """Convert one decoded IIFL market packet to the application quote model."""
    depth = tuple(
        LiveMarketDepth(
            price=float(level.price),
            quantity=int(level.quantity),
            order_count=int(level.orders),
            side=int(level.transaction_type),
        )
        for level in packet.depth[:10]
    )
    return LiveMarketQuote(
        last=float(packet.ltp),
        quantity=int(packet.last_traded_quantity),
        volume=int(packet.traded_volume),
        high=float(packet.high),
        low=float(packet.low),
        open=float(packet.open),
        close=float(packet.close),
        traded_at=packet.last_traded_time,
        bid=float(packet.best_bid_price) or None,
        bid_quantity=int(packet.best_bid_quantity) or None,
        ask=float(packet.best_ask_price) or None,
        ask_quantity=int(packet.best_ask_quantity) or None,
        depth=depth,
    )


def normalize_iifl_index_quote(packet: IndexTick) -> LiveMarketQuote:
    """Convert one decoded IIFL index packet to the application quote model."""
    return LiveMarketQuote(
        last=float(packet.ltp),
        quantity=int(packet.last_traded_quantity),
        volume=int(packet.traded_volume),
        high=float(packet.high),
        low=float(packet.low),
        open=float(packet.open),
        close=float(packet.close),
        traded_at=packet.last_traded_time,
    )


class IiflLiveFeedAdapter:
    """Connect to IIFL and share one connection across live-data consumers."""

    def __init__(
        self,
        session_store: SessionStore,
        instrument_catalog: BrokerInstrumentCatalogPort,
        *,
        tls_verify: bool,
        bridge_host: str,
        bridge_port: int,
        bridge_connect_timeout_seconds: float,
        bridge_reconnect_min_seconds: int,
        bridge_reconnect_max_seconds: int,
    ) -> None:
        """Create the adapter with explicit session and contract ports.

        Args:
            session_store: Source of the current authenticated IIFL session.
            instrument_catalog: Broker contract catalog used to resolve symbols.
            tls_verify: Whether to verify the IIFL bridge certificate.
            bridge_host: Configured IIFL MQTT host.
            bridge_port: Configured IIFL MQTT port.
            bridge_connect_timeout_seconds: Time allowed for MQTT connection.
            bridge_reconnect_min_seconds: Minimum reconnect delay.
            bridge_reconnect_max_seconds: Maximum reconnect delay.
        """
        self._sessions = session_store
        self._instrument_catalog = instrument_catalog
        self._bridge_tls_verify = tls_verify
        self._bridge_host = bridge_host
        self._bridge_port = bridge_port
        self._bridge_connect_timeout_seconds = bridge_connect_timeout_seconds
        self._bridge_reconnect_min_seconds = bridge_reconnect_min_seconds
        self._bridge_reconnect_max_seconds = bridge_reconnect_max_seconds
        self._bridge: BridgeClient | None = None
        self._retry_after = 0.0
        self._session_checked_at = 0.0
        self._lock = threading.Lock()
        self._listeners_lock = threading.Lock()
        self._feed_listener: FeedListener | None = None
        self._index_listener: IndexListener | None = None
        self._error_listener: FeedErrorListener | None = None
        self._feed_listeners: set[FeedListener] = set()
        self._index_listeners: set[IndexListener] = set()

    @property
    def is_connected(self) -> bool:
        """Return whether the current IIFL bridge is connected."""
        bridge = self._bridge
        return bool(bridge and bridge.is_connected)

    def resolve_contract_id(self, symbol: str, exchange: str) -> int | str:
        """Resolve one exact symbol and exchange in the cached IIFL master."""
        return self._instrument_catalog.contract_id(symbol, exchange)

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
            on_feed: Callback for instrument quote packets.
            on_index: Callback for index quote packets.
            on_error: Callback for broker feed errors.
            feed_topics: Instrument topics to restore after a new connection.
            index_topics: Index topics to restore after a new connection.

        Returns:
            Whether the shared connection is ready.
        """
        self._feed_listener = on_feed
        self._index_listener = on_index
        self._error_listener = on_error
        with self._lock:
            if self._bridge is not None:
                if time.monotonic() - self._session_checked_at < 1:
                    return bool(self._bridge.is_connected)
                self._session_checked_at = time.monotonic()
                session = self._sessions.load()
                old_session = self._bridge.session
                if old_session is None or (
                    session is not None
                    and session.user_session == old_session.user_session
                ):
                    return bool(self._bridge.is_connected)
                self._bridge.disconnect()
                self._bridge = None
                self._retry_after = 0.0

            if time.monotonic() < self._retry_after:
                return False

            bridge: BridgeClient | None = None
            try:
                session = self._sessions.load()
                if session is None or session.is_expired():
                    logger.debug("No active or unexpired IIFL session for live feed")
                    return False

                logger.info("Initializing IIFL BridgeClient for live ticks")
                bridge = BridgeClient(
                    session,
                    tls_verify=self._bridge_tls_verify,
                    host=self._bridge_host,
                    port=self._bridge_port,
                    reconnect_min_seconds=self._bridge_reconnect_min_seconds,
                    reconnect_max_seconds=self._bridge_reconnect_max_seconds,
                    client_id=session.preferred_username(),
                )
                bridge.on_feed = self._receive_feed
                bridge.on_index = self._receive_index
                bridge.on_error = self._dispatch_error
                bridge.connect(timeout=self._bridge_connect_timeout_seconds)
                self._bridge = bridge
                if feed_topics:
                    bridge.subscribe_feed(feed_topics)
                    logger.info("IIFL live feed subscribed to {} topics", len(feed_topics))
                if index_topics:
                    bridge.subscribe_index(index_topics)
                return True
            except Exception as exc:  # noqa: BLE001 - callers report feed unavailability
                if bridge is not None:
                    try:
                        bridge.disconnect()
                    except Exception:
                        logger.exception("Could not stop failed IIFL live feed")
                logger.warning("Could not connect IIFL live feed: {}", exc)
                self._bridge = None
                self._retry_after = time.monotonic() + 5.0
                return False

    def subscribe_feed(self, topics: list[str]) -> None:
        """Subscribe to instrument quote topics when connected."""
        bridge = self._bridge
        if bridge and bridge.is_connected:
            bridge.subscribe_feed(topics)

    def unsubscribe_feed(self, topics: list[str]) -> None:
        """Stop receiving instrument quote topics when connected."""
        bridge = self._bridge
        if bridge and bridge.is_connected:
            bridge.unsubscribe_feed(topics)

    def subscribe_index(self, topics: list[str]) -> None:
        """Subscribe to index quote topics when connected."""
        bridge = self._bridge
        if bridge and bridge.is_connected:
            bridge.subscribe_index(topics)

    def subscribe_open_interest(self, topics: list[str]) -> None:
        """Subscribe to derivative open-interest topics when connected."""
        bridge = self._bridge
        if bridge and bridge.is_connected:
            bridge.subscribe_open_interest(topics)

    def add_feed_listener(self, listener: FeedListener) -> None:
        """Register a consumer for instrument quote events."""
        with self._listeners_lock:
            self._feed_listeners.add(listener)

    def remove_feed_listener(self, listener: FeedListener) -> None:
        """Remove a consumer for instrument quote events."""
        with self._listeners_lock:
            self._feed_listeners.discard(listener)

    def add_index_listener(self, listener: IndexListener) -> None:
        """Register a consumer for index quote events."""
        with self._listeners_lock:
            self._index_listeners.add(listener)

    def remove_index_listener(self, listener: IndexListener) -> None:
        """Remove a consumer for index quote events."""
        with self._listeners_lock:
            self._index_listeners.discard(listener)

    def _receive_feed(self, topic: str, packet: MarketFeed) -> None:
        """Normalize one IIFL instrument packet before dispatching it."""
        quote = normalize_iifl_market_quote(packet)
        if self._feed_listener is not None:
            self._feed_listener(topic, quote)
        with self._listeners_lock:
            listeners = tuple(self._feed_listeners)
        for listener in listeners:
            listener(topic, quote)

    def _receive_index(self, topic: str, packet: IndexTick) -> None:
        """Normalize one IIFL index packet before dispatching it."""
        quote = normalize_iifl_index_quote(packet)
        if self._index_listener is not None:
            self._index_listener(topic, quote)
        with self._listeners_lock:
            listeners = tuple(self._index_listeners)
        for listener in listeners:
            listener(topic, quote)

    def _dispatch_error(self, code: int, message: str) -> None:
        """Send a feed error to the configured application callback."""
        if self._error_listener is not None:
            self._error_listener(code, message)
