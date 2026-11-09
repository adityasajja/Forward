"""Portfolio schema tables."""

from __future__ import annotations

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    String,
    Table,
    Text,
)

from forward.appdb.metadata import app_metadata

# ===========================================================================
# Portfolio tier — per-user capital allocation policy
# ===========================================================================
portfolio_policies = Table(
    "portfolio_policies",
    app_metadata,
    #: One row per user. User-scoped deliberately: ``system_state`` belongs to
    #: the installation (kill switch, execution mode), while capital
    #: allocation and portfolio limits belong to whoever owns the paper book.
    Column("user_id", String(32), ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True),
    #: The policy as JSON: portfolio limits, conflict handling and strategy
    #: priorities. Validated key-by-key on write by the portfolio service, so a
    #: stored row is always one the gate understands.
    Column("policy", Text, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    #: Who last changed it. A limit change with no actor is not an audit trail.
    Column("updated_by", String(64)),
    #: Free text, mandatory on write. Changing what the platform may risk is a
    #: consequential act; the caller enforces the requirement, the column holds it.
    Column("reason", Text),
)
