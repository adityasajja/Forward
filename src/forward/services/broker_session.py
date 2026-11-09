"""Broker session use cases for the login screen."""

from __future__ import annotations

from typing import Any

from forward.core.broker_session_ports import BrokerSessionPort


class BrokerSessionService:
    """Manage broker session status and user-driven login actions."""

    def __init__(self, session_port: BrokerSessionPort) -> None:
        """Create the broker-session service.

        Args:
            session_port: Adapter that reads and writes broker session state.
        """
        self._session_port = session_port

    def status(self) -> dict[str, Any]:
        """Return the current broker session state.

        Returns:
            Session details for the login page.
        """
        return self._session_port.status()

    def login(self, client_id: str, auth_code: str) -> dict[str, str]:
        """Create an authenticated session from a broker callback.

        Args:
            client_id: Broker client identifier from the redirect.
            auth_code: One-time authorization code from the redirect.

        Returns:
            The authenticated client ID and session expiry.
        """
        return self._session_port.create_session(client_id, auth_code)

    def logout(self) -> bool:
        """Clear the locally stored broker session.

        Returns:
            Whether a session file existed before it was cleared.
        """
        return self._session_port.clear_session()
