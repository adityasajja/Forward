"""Screener schema tables."""

from __future__ import annotations

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    Table,
    Text,
    UniqueConstraint,
)

from forward.appdb.metadata import app_metadata

# ===========================================================================
# Screener tier
# ===========================================================================
screener_scans = Table(
    "screener_scans",
    app_metadata,
    Column("scan_id", String(32), primary_key=True),
    Column("user_id", String(32), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
    Column("name", String(64), nullable=False),
    Column("description", Text),
    #: JSON: the nested condition tree, universe and ranking. The tree shape is
    #: owned by `forward.screener`, which validates it on the way in.
    Column("definition", Text, nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    UniqueConstraint("user_id", "name", name="uq_screener_user_name"),
    Index("ix_screener_user", "user_id"),
)
