"""Watchlists schema tables."""

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
    UniqueConstraint,
)

from forward.appdb.metadata import app_metadata

watchlists = Table(
    "watchlists",
    app_metadata,
    Column("watchlist_id", String(32), primary_key=True),
    Column("user_id", String(32), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
    Column("name", String(64), nullable=False),
    Column("exchange", String(16), nullable=False, default="NSEEQ"),
    Column("is_default", Boolean, nullable=False, default=False),
    Column("sort_order", Integer, nullable=False, default=0),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    UniqueConstraint("user_id", "name", name="uq_watchlist_user_name"),
    Index("ix_watchlists_user", "user_id"),
)

watchlist_items = Table(
    "watchlist_items",
    app_metadata,
    Column("item_id", String(32), primary_key=True),
    Column(
        "watchlist_id",
        String(32),
        ForeignKey("watchlists.watchlist_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("symbol", String(64), nullable=False),
    Column("sort_order", Integer, nullable=False, default=0),
    Column("note", String(255)),
    Column("added_at", DateTime, nullable=False),
    UniqueConstraint("watchlist_id", "symbol", name="uq_wl_item_symbol"),
    Index("ix_wl_items_order", "watchlist_id", "sort_order"),
)

watchlist_columns = Table(
    "watchlist_columns",
    app_metadata,
    Column("column_id", String(32), primary_key=True),
    Column(
        "watchlist_id",
        String(32),
        ForeignKey("watchlists.watchlist_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("key", String(32), nullable=False),
    Column("sort_order", Integer, nullable=False, default=0),
    Column("width", Integer),
    Column("visible", Boolean, nullable=False, default=True),
    UniqueConstraint("watchlist_id", "key", name="uq_wl_column_key"),
)

user_preferences = Table(
    "user_preferences",
    app_metadata,
    Column("user_id", String(32), ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True),
    Column("key", String(64), primary_key=True),
    Column("value", Text, nullable=False),
    Column("updated_at", DateTime, nullable=False),
)
