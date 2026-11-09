"""Schema for durable installation-wide state."""

from sqlalchemy import Column, DateTime, Index, String, Table, Text

from forward.appdb.metadata import app_metadata

system_state = Table(
    "system_state",
    app_metadata,
    # This state belongs to the installation, not a user. It stores the kill
    # switch and execution mode so a restart cannot silently reset either one.
    Column("key", String(64), primary_key=True),
    Column("value", Text),
    Column("updated_at", DateTime, nullable=False),
    Column("updated_by", String(64)),
    Column("reason", Text),
    Index("ix_system_state_updated", "updated_at"),
)
