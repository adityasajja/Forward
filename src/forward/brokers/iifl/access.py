"""Build reusable IIFL clients and broker interfaces."""

from __future__ import annotations

import threading
from typing import Any, cast

from forward.brokers.iifl.auth import SessionStore
from forward.brokers.iifl.broker import IiflBroker
from forward.brokers.iifl.client import IiflClient
from forward.brokers.iifl.contracts import InstrumentMaster
from forward.config.settings import Settings
from forward.core.broker_access_ports import AuthenticatedBrokerClientPort


class IiflBrokerAccessAdapter:
    """Create per-thread IIFL clients and their broker interfaces.

    Args:
        settings: Broker connection and session settings for this application.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._local = threading.local()

    def client(self) -> AuthenticatedBrokerClientPort:
        """Return this thread's reusable IIFL client."""
        client = getattr(self._local, "client", None)
        if client is None or client.is_closed:
            client = IiflClient(
                app_key=self._settings.iifl_app_key,
                app_secret=self._settings.iifl_app_secret,
                base_url=self._settings.iifl_base_url,
                timeout=self._settings.iifl_timeout,
                session_store=SessionStore(self._settings.iifl_session_cache),
                force_ipv4=self._settings.iifl_force_ipv4,
            )
            self._local.client = client
        return client

    def read_broker(self, client: AuthenticatedBrokerClientPort) -> Any:
        """Build the IIFL broker interface for read-only account access."""
        iifl_client = cast(IiflClient, client)
        return self._build_broker(iifl_client)

    def live_broker(self, client: AuthenticatedBrokerClientPort) -> Any:
        """Build the IIFL broker interface that can place orders."""
        iifl_client = cast(IiflClient, client)
        return self._build_broker(iifl_client)

    def _build_broker(self, client: IiflClient) -> IiflBroker:
        """Build a broker adapter with the configured order policy."""
        return IiflBroker(
            client,
            InstrumentMaster(client),
            default_product=self._settings.iifl_default_product,
            api_order_source=self._settings.iifl_api_order_source,
            market_protection_percent=self._settings.iifl_market_protection_percent,
        )
