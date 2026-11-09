"""Ports for account, session, and API-key storage."""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime
from typing import Any, Protocol


class SecretBoxPort(Protocol):
    """Encrypt and decrypt secrets stored by the auth use case."""

    def encrypt(self, plaintext: str) -> str:
        """Encrypt one secret for storage.

        Args:
            plaintext: Secret text to protect.

        Returns:
            A versioned encrypted value.
        """

class SecretBoxProviderPort(Protocol):
    """Build the configured secret box when authentication needs it."""

    def create(self) -> SecretBoxPort:
        """Load key material and create a secret box."""


class AuthSettingsPort(Protocol):
    """Provide authentication policy values from application configuration."""

    login_max_attempts: int
    login_lockout_minutes: int
    require_mfa_for_privileged: bool
    session_ttl_hours: int


class AuthUserStoragePort(Protocol):
    """Read and change account records inside one transaction."""

    def create(self, **fields: Any) -> dict[str, Any]:
        """Create an account from validated account fields."""

    def get(self, user_id: str) -> dict[str, Any] | None:
        """Return one account by its ID."""

    def get_by_identifier(self, identifier: str) -> dict[str, Any] | None:
        """Find an account by its email or username."""

    def list_all(self) -> list[dict[str, Any]]:
        """Return all account rows."""

    def update(self, user_id: str, **fields: Any) -> int:
        """Change account fields and return the number of changed rows."""

    def count(self) -> int:
        """Return the total account count."""

    def count_by_role(self, role: str) -> int:
        """Return the number of accounts with one role."""

    def register_failure(
        self, user_id: str, *, max_attempts: int, lockout_minutes: int
    ) -> dict[str, Any]:
        """Record a failed login and return updated lockout counters."""

    def register_success(self, user_id: str) -> None:
        """Clear failed-login counters after a successful login."""


class AuthSessionStoragePort(Protocol):
    """Read and change login sessions inside one transaction."""

    def create(
        self,
        *,
        user_id: str,
        token_hash: str,
        expires_at: datetime,
        ip: str | None,
        user_agent: str | None,
        mfa_satisfied: bool,
    ) -> dict[str, Any]:
        """Create a login session."""

    def get_by_token_hash(self, token_hash: str) -> dict[str, Any] | None:
        """Find a session by its token digest."""

    def get(self, session_id: str) -> dict[str, Any] | None:
        """Return one session by its ID."""

    def touch(self, session_id: str) -> None:
        """Update a session's last-seen time."""

    def set_mfa_satisfied(self, session_id: str, satisfied: bool) -> None:
        """Set whether a session passed the MFA gate."""

    def revoke(self, session_id: str) -> int:
        """Revoke one session and return the number of changed rows."""

    def revoke_all_for_user(self, user_id: str) -> int:
        """Revoke every session owned by a user."""

    def list_for_user(self, user_id: str) -> list[dict[str, Any]]:
        """Return the sessions owned by one user."""


class ApiKeyStoragePort(Protocol):
    """Read and change API keys inside one transaction."""

    def create(
        self,
        *,
        user_id: str,
        label: str,
        prefix: str,
        key_hash: str,
        scopes: list[str],
        expires_at: datetime | None,
    ) -> dict[str, Any]:
        """Create an API key record."""

    def get_by_hash(self, key_hash: str) -> dict[str, Any] | None:
        """Find an API key by its digest."""

    def list_for_user(self, user_id: str) -> list[dict[str, Any]]:
        """Return the API keys owned by one user."""

    def touch(self, key_id: str) -> None:
        """Update an API key's last-used time."""

    def revoke(self, key_id: str, user_id: str) -> int:
        """Revoke one API key owned by a user."""


class AuthTransactionPort(Protocol):
    """Group account operations and their audit event in one transaction."""

    users: AuthUserStoragePort
    sessions: AuthSessionStoragePort
    api_keys: ApiKeyStoragePort

    def record_audit(self, **event: Any) -> str:
        """Write an audit event as part of this transaction."""


class AuthStoragePort(Protocol):
    """Open account transactions and read the initial account count."""

    def user_count(self) -> int:
        """Return the account count without opening a use-case transaction."""

    def transaction(self) -> AbstractContextManager[AuthTransactionPort]:
        """Open one transaction for related identity changes."""
