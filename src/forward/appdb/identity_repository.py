"""SQL repositories for users, login sessions, and API keys."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session

from forward.appdb.account_schema import api_keys, sessions, users
from forward.appdb.engine import utcnow
from forward.appdb.repository_support import many, new_id, one


# --------------------------------------------------------------------------- users
class UserRepository:
    """Accounts. Password material is opaque here — hashing lives in ``forward.auth``."""

    @staticmethod
    def create(
        session: Session,
        *,
        email: str,
        username: str,
        password_hash: str,
        role: str,
        display_name: str | None = None,
        mfa_secret: str | None = None,
    ) -> dict[str, Any]:
        now = utcnow()
        row = {
            "user_id": new_id(),
            # Normalised on write so uniqueness is case-insensitive without a
            # functional index (which SQLite and Postgres spell differently).
            "email": email.strip().lower(),
            "username": username.strip(),
            "display_name": display_name or username.strip(),
            "password_hash": password_hash,
            "role": role,
            "is_active": True,
            "mfa_enabled": False,
            "mfa_secret": mfa_secret,
            "failed_logins": 0,
            "locked_until": None,
            "created_at": now,
            "updated_at": now,
            "last_login_at": None,
        }
        session.execute(insert(users).values(**row))
        return row

    @staticmethod
    def get(session: Session, user_id: str) -> dict[str, Any] | None:
        return one(session.execute(select(users).where(users.c.user_id == user_id)).first())

    @staticmethod
    def get_by_identifier(session: Session, identifier: str) -> dict[str, Any] | None:
        """Look up by email or username, case-insensitively on either."""
        ident = identifier.strip()
        stmt = select(users).where((users.c.email == ident.lower()) | (users.c.username == ident))
        return one(session.execute(stmt).first())

    @staticmethod
    def list_all(session: Session) -> list[dict[str, Any]]:
        return many(session.execute(select(users).order_by(users.c.created_at)).all())

    @staticmethod
    def update(session: Session, user_id: str, **fields: Any) -> int:
        if not fields:
            return 0
        if "email" in fields and fields["email"]:
            fields["email"] = str(fields["email"]).strip().lower()
        fields["updated_at"] = utcnow()
        result = session.execute(update(users).where(users.c.user_id == user_id).values(**fields))
        return int(result.rowcount or 0)

    @staticmethod
    def count(session: Session) -> int:
        return int(session.execute(select(func.count()).select_from(users)).scalar() or 0)

    @staticmethod
    def count_by_role(session: Session, role: str) -> int:
        stmt = select(func.count()).select_from(users).where(users.c.role == role)
        return int(session.execute(stmt).scalar() or 0)

    @staticmethod
    def register_failure(
        session: Session, user_id: str, *, max_attempts: int, lockout_minutes: int
    ) -> dict[str, Any]:
        """Increment the failure counter, locking the account at the threshold.

        Returns the post-update counters so the caller can tell the user how many
        attempts remain. The lock is time-based rather than permanent so a
        legitimate user who fat-fingered a password is not locked out forever,
        and so support has an answer that is not "edit the database".
        """
        current = UserRepository.get(session, user_id)
        if current is None:
            return {"failed_logins": 0, "locked_until": None}
        failed = int(current.get("failed_logins") or 0) + 1
        locked_until = None
        if failed >= max_attempts:
            locked_until = utcnow() + timedelta(minutes=lockout_minutes)
        session.execute(
            update(users)
            .where(users.c.user_id == user_id)
            .values(failed_logins=failed, locked_until=locked_until, updated_at=utcnow())
        )
        return {"failed_logins": failed, "locked_until": locked_until}

    @staticmethod
    def register_success(session: Session, user_id: str) -> None:
        session.execute(
            update(users)
            .where(users.c.user_id == user_id)
            .values(
                failed_logins=0,
                locked_until=None,
                last_login_at=utcnow(),
                updated_at=utcnow(),
            )
        )


# ------------------------------------------------------------------------ sessions
class SessionRepository:
    """Store login-session records."""

    @staticmethod
    def create(
        session: Session,
        *,
        user_id: str,
        token_hash: str,
        expires_at: datetime,
        ip: str | None = None,
        user_agent: str | None = None,
        mfa_satisfied: bool = True,
    ) -> dict[str, Any]:
        now = utcnow()
        row = {
            "session_id": new_id(),
            "user_id": user_id,
            "token_hash": token_hash,
            "created_at": now,
            "expires_at": expires_at,
            "last_seen_at": now,
            "revoked_at": None,
            "ip": (ip or "")[:64] or None,
            "user_agent": (user_agent or "")[:255] or None,
            "mfa_satisfied": mfa_satisfied,
        }
        session.execute(insert(sessions).values(**row))
        return row

    @staticmethod
    def get_by_token_hash(session: Session, token_hash: str) -> dict[str, Any] | None:
        stmt = select(sessions).where(sessions.c.token_hash == token_hash)
        return one(session.execute(stmt).first())

    @staticmethod
    def get(session: Session, session_id: str) -> dict[str, Any] | None:
        stmt = select(sessions).where(sessions.c.session_id == session_id)
        return one(session.execute(stmt).first())

    @staticmethod
    def touch(session: Session, session_id: str, when: datetime | None = None) -> None:
        session.execute(
            update(sessions)
            .where(sessions.c.session_id == session_id)
            .values(last_seen_at=when or utcnow())
        )

    @staticmethod
    def set_mfa_satisfied(session: Session, session_id: str, satisfied: bool = True) -> None:
        session.execute(
            update(sessions)
            .where(sessions.c.session_id == session_id)
            .values(mfa_satisfied=satisfied)
        )

    @staticmethod
    def revoke(session: Session, session_id: str) -> int:
        result = session.execute(
            update(sessions)
            .where(sessions.c.session_id == session_id, sessions.c.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )
        return int(result.rowcount or 0)

    @staticmethod
    def revoke_all_for_user(session: Session, user_id: str) -> int:
        result = session.execute(
            update(sessions)
            .where(sessions.c.user_id == user_id, sessions.c.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )
        return int(result.rowcount or 0)

    @staticmethod
    def list_for_user(
        session: Session, user_id: str, *, active_only: bool = True
    ) -> list[dict[str, Any]]:
        stmt = select(sessions).where(sessions.c.user_id == user_id)
        if active_only:
            stmt = stmt.where(sessions.c.revoked_at.is_(None), sessions.c.expires_at > utcnow())
        return many(session.execute(stmt.order_by(sessions.c.created_at.desc())).all())


# ------------------------------------------------------------------------ api keys
class ApiKeyRepository:
    """Store user-owned API-key records."""

    @staticmethod
    def create(
        session: Session,
        *,
        user_id: str,
        label: str,
        prefix: str,
        key_hash: str,
        scopes: list[str],
        expires_at: datetime | None = None,
    ) -> dict[str, Any]:
        row = {
            "key_id": new_id(),
            "user_id": user_id,
            "label": label[:64],
            "prefix": prefix[:12],
            "key_hash": key_hash,
            "scopes": ",".join(sorted(set(scopes))),
            "created_at": utcnow(),
            "expires_at": expires_at,
            "last_used_at": None,
            "revoked_at": None,
        }
        session.execute(insert(api_keys).values(**row))
        return row

    @staticmethod
    def get_by_hash(session: Session, key_hash: str) -> dict[str, Any] | None:
        stmt = select(api_keys).where(api_keys.c.key_hash == key_hash)
        return one(session.execute(stmt).first())

    @staticmethod
    def list_for_user(session: Session, user_id: str) -> list[dict[str, Any]]:
        stmt = (
            select(api_keys)
            .where(api_keys.c.user_id == user_id)
            .order_by(api_keys.c.created_at.desc())
        )
        return many(session.execute(stmt).all())

    @staticmethod
    def touch(session: Session, key_id: str) -> None:
        session.execute(
            update(api_keys).where(api_keys.c.key_id == key_id).values(last_used_at=utcnow())
        )

    @staticmethod
    def revoke(session: Session, key_id: str, user_id: str) -> int:
        result = session.execute(
            update(api_keys)
            .where(
                api_keys.c.key_id == key_id,
                api_keys.c.user_id == user_id,
                api_keys.c.revoked_at.is_(None),
            )
            .values(revoked_at=utcnow())
        )
        return int(result.rowcount or 0)
