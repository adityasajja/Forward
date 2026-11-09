"""SQL adapter for authentication storage."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from forward.appdb.audit_storage import record_audit_event
from forward.appdb.engine import AppDatabase
from forward.appdb.identity_repository import (
    ApiKeyRepository,
    SessionRepository,
    UserRepository,
)
from forward.core.audit_ports import AuditLogPort
from forward.core.auth_ports import AuthStoragePort, AuthTransactionPort
from forward.core.errors import DuplicateAuthIdentity


class SqlAuthStorageAdapter(AuthStoragePort):
    """Store authentication records in the application database."""

    def __init__(self, database: AppDatabase, audit_log: AuditLogPort) -> None:
        """Create the adapter.

        Args:
            database: Application database that stores identity records.
            audit_log: Append-only file audit adapter.
        """
        self._database = database
        self._audit_log = audit_log

    def user_count(self) -> int:
        """Return the number of accounts in the database."""
        return self._database.user_count()

    @contextmanager
    def transaction(self) -> Iterator[AuthTransactionPort]:
        """Open a managed transaction for authentication operations."""
        with self._database.session() as session:
            yield SqlAuthTransaction(session, self._audit_log)


class SqlAuthTransaction(AuthTransactionPort):
    """Expose SQL repositories through authentication storage ports."""

    def __init__(self, session: Session, audit_log: AuditLogPort) -> None:
        """Create the transaction adapters.

        Args:
            session: Open application database transaction.
            audit_log: Adapter for the append-only file audit trail.
        """
        self._session = session
        self._audit_log = audit_log
        self.users = SqlAuthUserStorage(session)
        self.sessions = SqlAuthSessionStorage(session)
        self.api_keys = SqlApiKeyStorage(session)

    def record_audit(self, **event: Any) -> str:
        """Write an audit event to the configured sinks.

        Args:
            **event: Fields accepted by the audit recorder.

        Returns:
            The event identifier from the database sink, if it was written.
        """
        return record_audit_event(self._session, self._audit_log, **event)


class SqlAuthUserStorage:
    """Adapt account repository calls to the authentication port."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, **fields: Any) -> dict[str, Any]:
        try:
            return UserRepository.create(self._session, **fields)
        except IntegrityError as exc:
            raise DuplicateAuthIdentity from exc

    def get(self, user_id: str) -> dict[str, Any] | None:
        return UserRepository.get(self._session, user_id)

    def get_by_identifier(self, identifier: str) -> dict[str, Any] | None:
        return UserRepository.get_by_identifier(self._session, identifier)

    def list_all(self) -> list[dict[str, Any]]:
        return UserRepository.list_all(self._session)

    def update(self, user_id: str, **fields: Any) -> int:
        try:
            return UserRepository.update(self._session, user_id, **fields)
        except IntegrityError as exc:
            raise DuplicateAuthIdentity from exc

    def count(self) -> int:
        return UserRepository.count(self._session)

    def count_by_role(self, role: str) -> int:
        return UserRepository.count_by_role(self._session, role)

    def register_failure(
        self, user_id: str, *, max_attempts: int, lockout_minutes: int
    ) -> dict[str, Any]:
        return UserRepository.register_failure(
            self._session,
            user_id,
            max_attempts=max_attempts,
            lockout_minutes=lockout_minutes,
        )

    def register_success(self, user_id: str) -> None:
        UserRepository.register_success(self._session, user_id)


class SqlAuthSessionStorage:
    """Adapt login-session repository calls to the authentication port."""

    def __init__(self, session: Session) -> None:
        self._session = session

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
        return SessionRepository.create(
            self._session,
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            ip=ip,
            user_agent=user_agent,
            mfa_satisfied=mfa_satisfied,
        )

    def get_by_token_hash(self, token_hash: str) -> dict[str, Any] | None:
        return SessionRepository.get_by_token_hash(self._session, token_hash)

    def get(self, session_id: str) -> dict[str, Any] | None:
        return SessionRepository.get(self._session, session_id)

    def touch(self, session_id: str) -> None:
        SessionRepository.touch(self._session, session_id)

    def set_mfa_satisfied(self, session_id: str, satisfied: bool) -> None:
        SessionRepository.set_mfa_satisfied(self._session, session_id, satisfied)

    def revoke(self, session_id: str) -> int:
        return SessionRepository.revoke(self._session, session_id)

    def revoke_all_for_user(self, user_id: str) -> int:
        return SessionRepository.revoke_all_for_user(self._session, user_id)

    def list_for_user(self, user_id: str) -> list[dict[str, Any]]:
        return SessionRepository.list_for_user(self._session, user_id)


class SqlApiKeyStorage:
    """Adapt API-key repository calls to the authentication port."""

    def __init__(self, session: Session) -> None:
        self._session = session

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
        return ApiKeyRepository.create(
            self._session,
            user_id=user_id,
            label=label,
            prefix=prefix,
            key_hash=key_hash,
            scopes=scopes,
            expires_at=expires_at,
        )

    def get_by_hash(self, key_hash: str) -> dict[str, Any] | None:
        return ApiKeyRepository.get_by_hash(self._session, key_hash)

    def list_for_user(self, user_id: str) -> list[dict[str, Any]]:
        return ApiKeyRepository.list_for_user(self._session, user_id)

    def touch(self, key_id: str) -> None:
        ApiKeyRepository.touch(self._session, key_id)

    def revoke(self, key_id: str, user_id: str) -> int:
        return ApiKeyRepository.revoke(self._session, key_id, user_id)
