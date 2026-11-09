"""Audit schema tables."""

from __future__ import annotations

from sqlalchemy import (
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Table,
    Text,
)

from forward.appdb.metadata import app_metadata

audit_events = Table(
    "audit_events",
    app_metadata,
    Column("event_id", String(32), primary_key=True),
    Column("ts", DateTime, nullable=False),
    Column("user_id", String(32)),
    #: Denormalised on purpose: the actor's email must survive the user row being
    #: deleted, or the trail stops explaining who did what.
    Column("actor", String(64)),
    Column("action", String(64), nullable=False),
    Column("target_type", String(32)),
    Column("target_id", String(64)),
    Column("strategy_id", String(32)),
    Column("strategy_version", Integer),
    Column("request_id", String(32)),
    Column("result", String(16), nullable=False),
    Column("detail", Text),
    Column("ip", String(64)),
    Index("ix_audit_ts", "ts"),
    Index("ix_audit_user_ts", "user_id", "ts"),
    Index("ix_audit_action", "action"),
)
