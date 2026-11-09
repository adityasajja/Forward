"""IIFL adapter for broker login and session storage."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from forward.brokers.iifl.auth import SessionStore, login_url
from forward.brokers.iifl.client import IiflClient


class IiflSessionAdapter:
    """Manage the IIFL session through its auth API and local session file."""

    def __init__(
        self,
        *,
        app_key: str,
        app_secret: str,
        base_url: str,
        session_path: str | Path,
        redirect_url: str | None,
        timeout: float,
        force_ipv4: bool,
    ) -> None:
        """Create the session adapter.

        Args:
            app_key: IIFL application key.
            app_secret: IIFL application secret.
            base_url: IIFL API base URL.
            session_path: Path for the stored broker session.
            redirect_url: Optional broker callback URL.
        """
        self._app_key = app_key
        self._app_secret = app_secret
        self._base_url = base_url
        self._session_store = SessionStore(session_path)
        self._redirect_url = redirect_url
        self._timeout = timeout
        self._force_ipv4 = force_ipv4

    def status(self) -> dict[str, Any]:
        """Return the current broker session and login URL.

        Returns:
            Session state and login information for the UI.
        """
        session = self._session_store.load()
        return {
            "session_active": session is not None,
            "client_id": session.client_id if session else None,
            "expires_at": session.expires_at.isoformat() if session else None,
            "login_url": login_url(self._app_key, self._redirect_url),
        }

    def create_session(self, client_id: str, auth_code: str) -> dict[str, str]:
        """Exchange the callback credentials for an IIFL session.

        Args:
            client_id: Broker client identifier from the redirect.
            auth_code: One-time authorization code from the redirect.

        Returns:
            The authenticated client ID and session expiry.
        """
        client = IiflClient(
            app_key=self._app_key,
            app_secret=self._app_secret,
            base_url=self._base_url,
            timeout=self._timeout,
            session_store=self._session_store,
            force_ipv4=self._force_ipv4,
        )
        session = client.create_session(client_id, auth_code)
        return {
            "client_id": session.client_id,
            "expires_at": session.expires_at.isoformat(),
        }

    def clear_session(self) -> bool:
        """Clear the local session file without calling the broker logout API.

        Returns:
            Whether a session file existed before it was cleared.
        """
        had_session = self._session_store.path.exists()
        self._session_store.clear()
        return had_session
