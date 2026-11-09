"""IIFL market-data and simulated-order adapter for options paper runs."""

from __future__ import annotations

from typing import Any

from forward.brokers.iifl.feeds import IiflLiveFeed
from forward.brokers.paper import QuotePaperBroker
from forward.core.live_feed_ports import LiveFeedConnectionPort


class IiflOptionsForwardAdapter:
    """Build a live IIFL quote feed and a quote-driven paper broker."""

    def __init__(
        self,
        *,
        tls_verify: bool,
        max_quote_age_seconds: float,
        bridge_host: str,
        bridge_port: int,
        bridge_connect_timeout_seconds: float,
        bridge_reconnect_min_seconds: int,
        bridge_reconnect_max_seconds: int,
        frequency: str,
    ) -> None:
        """Create the adapter with explicit feed and quote settings.

        Args:
            tls_verify: Whether the IIFL bridge must verify its TLS certificate.
            max_quote_age_seconds: Maximum quote age accepted for simulated fills.
            bridge_host: Configured IIFL MQTT host.
            bridge_port: Configured IIFL MQTT port.
            bridge_connect_timeout_seconds: Time allowed for MQTT connection.
            bridge_reconnect_min_seconds: Minimum reconnect delay.
            bridge_reconnect_max_seconds: Maximum reconnect delay.
            frequency: Candle frequency requested from the market-data API.
        """
        self._tls_verify = tls_verify
        self._max_quote_age_seconds = max_quote_age_seconds
        self._bridge_host = bridge_host
        self._bridge_port = bridge_port
        self._bridge_connect_timeout_seconds = bridge_connect_timeout_seconds
        self._bridge_reconnect_min_seconds = bridge_reconnect_min_seconds
        self._bridge_reconnect_max_seconds = bridge_reconnect_max_seconds
        self._frequency = frequency

    def create_feed(
        self,
        session: Any,
        instruments: dict[str, Any],
        bridge_client: LiveFeedConnectionPort,
    ) -> IiflLiveFeed:
        """Create the configured IIFL feed for the selected instruments.

        Args:
            session: The authenticated IIFL session.
            instruments: Instruments requested by the strategy.
            bridge_client: The already connected shared live-feed connection.

        Returns:
            The live feed adapter.
        """
        return IiflLiveFeed(
            session,
            instruments,
            tls_verify=self._tls_verify,
            bridge_host=self._bridge_host,
            bridge_port=self._bridge_port,
            bridge_connect_timeout_seconds=self._bridge_connect_timeout_seconds,
            bridge_reconnect_min_seconds=self._bridge_reconnect_min_seconds,
            bridge_reconnect_max_seconds=self._bridge_reconnect_max_seconds,
            freq=self._frequency,
            bridge_client=bridge_client,
        )

    def create_paper_broker(self, feed: IiflLiveFeed, commission: Any) -> QuotePaperBroker:
        """Create a simulated broker that fills only from live quotes.

        Args:
            feed: The connected quote feed.
            commission: The configured commission model.

        Returns:
            A broker that never submits real orders.
        """
        return QuotePaperBroker(
            feed.last_tick,
            commission=commission,
            max_quote_age_seconds=self._max_quote_age_seconds,
        )
