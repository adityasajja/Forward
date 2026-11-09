"""Execution schema tables."""

from __future__ import annotations

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)

from forward.appdb.metadata import app_metadata

# ===========================================================================
# Trading tier — the order management system
# ===========================================================================
#
# Two design decisions worth stating, because both are load-bearing and both
# differ from the obvious first draft:
#
# 1. **`orders.status` is a projection, not the truth.** The truth is the
#    `order_events` log; the column is a cache of the last event's `to_status`,
#    written inside the same transaction as that event. It exists so a list view
#    does not have to fold the whole log, and there is a test asserting the two
#    agree. The brief's rule — "do not treat order status as a single mutable
#    field" — is satisfied by the log being authoritative and the column being
#    derived, not by removing the column.
#
# 2. **There is no `fills` table.** A fill *is* an `order_events` row whose
#    `to_status` is PARTIALLY_FILLED or FILLED with `filled_qty` and
#    `filled_price` set. Positions and P&L are folds over those rows. A separate
#    fill table would be a second copy of the same facts with no way to tell
#    which one is right when they disagree. (The Postgres `orders`/`fills` DDL in
#    `forward/data/store.py` is superseded by these tables — it was never called by
#    anything, and is now documented as deprecated.)

orders = Table(
    "orders",
    app_metadata,
    Column("order_id", String(32), primary_key=True),
    Column("user_id", String(32), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
    #: Which deployment produced this order. Null for a manual order.
    Column("deployment_id", String(32), ForeignKey("deployments.deployment_id", ondelete="SET NULL")),
    Column("strategy_id", String(32)),
    Column("strategy_version", Integer),
    Column("signal_id", String(32)),
    #: Ties the whole chain together: signal → risk → intent → order → fill.
    Column("correlation_id", String(32)),
    Column("symbol", String(64), nullable=False),
    Column("exchange", String(16), nullable=False, default="NSEEQ"),
    Column("asset_class", String(16), nullable=False, default="EQUITY"),
    Column("side", String(4), nullable=False),
    Column("quantity", Float, nullable=False),
    Column("order_type", String(12), nullable=False, default="MARKET"),
    Column("limit_price", Float),
    Column("stop_price", Float),
    Column("tif", String(8), nullable=False, default="DAY"),
    Column("product", String(16)),
    #: PAPER or LIVE. A paper order and a live order are the same shape; only the
    #: venue differs, which is what makes the paper engine worth having.
    Column("mode", String(8), nullable=False, default="PAPER"),
    #: Projection of the newest event. See note 1 above.
    Column("status", String(24), nullable=False),
    Column("broker_order_id", String(64)),
    Column("filled_quantity", Float, nullable=False, default=0.0),
    Column("avg_fill_price", Float, nullable=False, default=0.0),
    #: Price at the moment the order was raised, for slippage against the fill.
    Column("requested_price", Float),
    Column("tag", String(64)),
    Column("reject_reason", String(255)),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    Column("submitted_at", DateTime),
    Column("completed_at", DateTime),
    Index("ix_orders_user_created", "user_id", "created_at"),
    Index("ix_orders_status", "status"),
    Index("ix_orders_broker", "broker_order_id"),
    Index("ix_orders_deployment", "deployment_id"),
    Index("ix_orders_correlation", "correlation_id"),
)

order_events = Table(
    "order_events",
    app_metadata,
    Column("order_event_id", String(32), primary_key=True),
    Column("order_id", String(32), ForeignKey("orders.order_id", ondelete="CASCADE"), nullable=False),
    #: Monotonic per order, and the reason the lifecycle is *deterministically*
    #: reconstructable. Two transitions can share a timestamp — a reject is
    #: written microseconds after the submit — so ordering by `ts` alone is a
    #: tie-break waiting to happen. `UNIQUE(order_id, seq)` makes the log a
    #: sequence rather than a bag of timestamps.
    Column("seq", Integer, nullable=False),
    Column("from_status", String(24)),
    Column("to_status", String(24), nullable=False),
    Column("ts", DateTime, nullable=False),
    #: Exchange timestamp, when the broker supplies one.
    Column("broker_ts", DateTime),
    Column("ack_ts", DateTime),
    Column("fill_ts", DateTime),
    Column("requested_price", Float),
    Column("filled_price", Float),
    Column("filled_qty", Float),
    Column("slippage_bps", Float),
    #: The frictions actually charged on this fill, in rupees. Recorded rather
    #: than recomputed from a cost model at read time: a paper fill's commission
    #: is a *fact about what happened*, and recomputing it would silently restate
    #: history every time a rate changed. It is also what lets the paper ledger be
    #: a faithful fold over this table — positions, cash and net P&L all derive
    #: from rows here, with no second state store to drift.
    Column("commission", Float),
    #: signal→submit, submit→ack, ack→fill. Stored per transition so execution
    #: latency is a measurement rather than a claim.
    Column("latency_ms", Integer),
    Column("reject_reason", String(255)),
    #: The broker's own payload, kept verbatim for post-mortems.
    Column("raw", Text),
    #: Who or what caused the transition: "oms", "broker", "reconciler", "user".
    Column("source", String(16), nullable=False, default="oms"),
    Column("correlation_id", String(32)),
    Column("request_id", String(32)),
    UniqueConstraint("order_id", "seq", name="uq_order_event_seq"),
    Index("ix_order_events_order", "order_id", "seq"),
    Index("ix_order_events_ts", "ts"),
)

order_intents = Table(
    "order_intents",
    app_metadata,
    Column(
        "idempotency_key",
        String(64),
        primary_key=True,
    ),
    Column("order_id", String(32), ForeignKey("orders.order_id", ondelete="CASCADE"), nullable=False),
    Column("user_id", String(32), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
    Column("strategy_id", String(32)),
    Column("strategy_version", Integer),
    Column("signal_id", String(32)),
    Column("created_at", DateTime, nullable=False),
    #: The primary key *is* the mechanism. There is deliberately no separate
    #: unique index on `order_id`: one intent maps to one order, and a second
    #: intent for the same order would be a bug rather than a constraint
    #: violation, so it is caught in code where the message can explain itself.
    Index("ix_order_intents_order", "order_id"),
)

deployments = Table(
    "deployments",
    app_metadata,
    Column("deployment_id", String(32), primary_key=True),
    Column("user_id", String(32), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
    Column("strategy_id", String(32), nullable=False),
    Column("strategy_version", Integer, nullable=False),
    Column("mode", String(8), nullable=False),
    Column("status", String(12), nullable=False),
    Column("capital", Float, nullable=False),
    Column("broker_account", String(32)),
    #: JSON: symbols, exchange, risk overrides, session window. Enough to
    #: reproduce the deployment's behaviour from the row alone.
    Column("config", Text),
    Column("started_at", DateTime),
    Column("stopped_at", DateTime),
    Column("stop_reason", String(255)),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    Index("ix_deployments_user", "user_id"),
    Index("ix_deployments_strategy", "strategy_id", "strategy_version"),
    Index("ix_deployments_status", "status"),
)

reconciliation_runs = Table(
    "reconciliation_runs",
    app_metadata,
    Column("run_id", String(32), primary_key=True),
    Column("user_id", String(32), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
    Column("ts", DateTime, nullable=False),
    #: orders | positions | holdings | funds | all
    Column("scope", String(32), nullable=False),
    Column("internal_count", Integer),
    Column("broker_count", Integer),
    Column("mismatches", Integer, nullable=False, default=0),
    #: ok | warning | critical. A run with mismatches is never silently `ok`.
    Column("severity", String(16), nullable=False, default="ok"),
    #: JSON list of individual differences, so a mismatch is a fact with a shape
    #: rather than a log line someone has to grep for.
    Column("detail", Text),
    Column("error", String(255)),
    Column("duration_ms", Integer),
    Index("ix_recon_ts", "ts"),
    Index("ix_recon_user_ts", "user_id", "ts"),
    Index("ix_recon_severity", "severity"),
)
