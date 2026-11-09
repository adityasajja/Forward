"""Port for broker session access."""

from __future__ import annotations

from typing import Any, Protocol


class BrokerSessionPort(Protocol):
    """Read, create, and clear the broker login session."""

    def status(self) -> dict[str, Any]:
        """Return the current session state and login URL."""

    def create_session(self, client_id: str, auth_code: str) -> dict[str, str]:
        """Create a session from the broker redirect.

        Args:
            client_id: Broker client identifier from the redirect.
            auth_code: One-time authorization code from the redirect.

        Returns:
            The authenticated client ID and session expiry.
        """

    def clear_session(self) -> bool:
        """Clear the saved broker session.

        Returns:
            Whether a saved session existed.
        """
