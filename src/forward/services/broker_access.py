"""Apply session and environment rules before broker access."""

from __future__ import annotations

from typing import Any

from forward.core.broker_access_ports import AuthenticatedBrokerClientPort, BrokerAccessProviderPort
from forward.core.errors import BrokerUnavailable


class BrokerAccessService:
    """Give callers broker access that follows platform rules.

    Args:
        provider: Adapter that creates broker clients and broker interfaces.
        environment: Environment name used to block live orders in development.
    """

    def __init__(self, provider: BrokerAccessProviderPort, environment: str) -> None:
        self._provider = provider
        self._environment = environment

    def authenticated_client(self) -> AuthenticatedBrokerClientPort:
        """Return this worker's client after restoring its saved session.

        Raises:
            BrokerUnavailable: If the client has no saved broker session.
        """
        client = self._provider.client()
        if client.restore_session() is None:
            raise BrokerUnavailable(
                "no active IIFL session — run `forward login`",
                status=401,
                code="no_broker_session",
            )
        return client

    def read_broker(self) -> Any:
        """Return a broker interface for read-only account data."""
        client = self.authenticated_client()
        return self._provider.read_broker(client)

    def live_broker(self) -> Any:
        """Return an order-capable broker outside the development environment.

        Raises:
            BrokerUnavailable: If live access is blocked or there is no session.
        """
        if self._environment == "dev":
            raise BrokerUnavailable(
                "live endpoints disabled in dev — set ENV=paper|live",
                status=403,
                code="live_disabled_in_dev",
            )
        client = self.authenticated_client()
        return self._provider.live_broker(client)
