"""Ports for options forward execution."""

from __future__ import annotations

from typing import Any, Protocol

from forward.core.live_feed_ports import LiveFeedConnectionPort


class OptionsForwardExecutionPort(Protocol):
    """Create the market feed and simulated broker for forward options runs."""

    def create_feed(
        self,
        session: Any,
        instruments: dict[str, Any],
        bridge_client: LiveFeedConnectionPort,
    ) -> Any:
        """Create a live quote feed for the selected instruments.

        Args:

            session: The authenticated broker session.

            instruments: Spot and option instruments requested by the strategy.

            bridge_client: The shared live-feed connection.

        Returns:

            A live feed for the requested instruments.
        """

    def create_paper_broker(self, feed: Any, commission: Any) -> Any:
        """Create a broker that simulates fills from live quotes.

        Args:

            feed: The run's live quote feed.

            commission: The configured fee model.

        Returns:

            A simulated broker that does not send exchange orders.
        """
