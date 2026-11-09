"""Ports for broker access."""

from __future__ import annotations

from typing import Any, Protocol


class AuthenticatedBrokerClientPort(Protocol):
    """Restore a saved broker session."""

    @property
    def is_closed(self) -> bool:
        """Return whether the client transport is closed."""

    def restore_session(self) -> Any | None:
        """Restore the saved session.

        Returns:
            The authenticated session, or None when no session is saved.
        """


class BrokerAccessProviderPort(Protocol):
    """Create the broker adapters used by the app."""

    def client(self) -> AuthenticatedBrokerClientPort:
        """Return the reusable broker client for this worker."""

    def read_broker(self, client: AuthenticatedBrokerClientPort) -> Any:
        """Create an account-read adapter for an authenticated client.

        Args:
            client: Authenticated broker client.

        Returns:
            Adapter for account reads.
        """

    def live_broker(self, client: AuthenticatedBrokerClientPort) -> Any:
        """Create an order adapter for an authenticated client.

        Args:
            client: Authenticated broker client.

        Returns:
            Adapter for live broker orders.
        """
