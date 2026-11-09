"""Account schema tables."""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
)

from forward.appdb.metadata import app_metadata

users = Table(
    "users",
    app_metadata,
    Column("user_id", String(32), primary_key=True),
    Column("email", String(255), nullable=False, unique=True),
    Column("username", String(64), nullable=False, unique=True),
    Column("display_name", String(128)),
    Column("password_hash", Text, nullable=False),
    Column("role", String(16), nullable=False),
    Column("is_active", Boolean, nullable=False, default=True),
    Column("mfa_enabled", Boolean, nullable=False, default=False),
    #: Base32 TOTP seed. Stored encrypted when a secret key is configured; the
    #: encryption is a property of the service, not of this table.
    Column("mfa_secret", Text),
    #: Highest TOTP counter already accepted. Without it, a code observed in
    #: transit can be replayed for the remainder of its 30-second window.
    Column("mfa_last_counter", Integer),
    Column("failed_logins", Integer, nullable=False, default=0),
    Column("locked_until", DateTime),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    Column("last_login_at", DateTime),
)

sessions = Table(
    "sessions",
    app_metadata,
    Column("session_id", String(32), primary_key=True),
    Column("user_id", String(32), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
    #: sha256(raw token). A database dump does not yield a usable session.
    Column("token_hash", String(64), nullable=False, unique=True),
    Column("created_at", DateTime, nullable=False),
    Column("expires_at", DateTime, nullable=False),
    Column("last_seen_at", DateTime),
    Column("revoked_at", DateTime),
    Column("ip", String(64)),
    Column("user_agent", String(255)),
    #: False for a password-only login that still owes a second factor. Such a
    #: session may enrol MFA but is refused by every permission check.
    Column("mfa_satisfied", Boolean, nullable=False, default=False),
    Index("ix_sessions_user", "user_id"),
    Index("ix_sessions_expires", "expires_at"),
)

api_keys = Table(
    "api_keys",
    app_metadata,
    Column("key_id", String(32), primary_key=True),
    Column("user_id", String(32), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
    Column("label", String(64), nullable=False),
    #: The only part of the key that is stored in the clear, so a UI can show
    #: "which key is this" without the row being usable as a credential.
    Column("prefix", String(12), nullable=False),
    Column("key_hash", String(64), nullable=False, unique=True),
    Column("scopes", Text, nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("expires_at", DateTime),
    Column("last_used_at", DateTime),
    Column("revoked_at", DateTime),
    Index("ix_api_keys_user", "user_id"),
)
