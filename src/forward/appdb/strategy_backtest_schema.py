"""Strategy backtest schema tables."""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
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
# Strategy tier
# ===========================================================================
strategies = Table(
    "strategies",
    app_metadata,
    Column("strategy_id", String(32), primary_key=True),
    Column("user_id", String(32), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
    Column("name", String(128), nullable=False),
    Column("description", Text),
    #: Values are defined by ``forward.core.strategy_kinds.STRATEGY_KINDS``.
    Column("kind", String(16), nullable=False),
    #: For a `code` strategy, the registry key it maps to (e.g. "ema_cross").
    #: Null for a strategy defined entirely by its JSON rules.
    Column("engine_key", String(64)),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    Column("archived_at", DateTime),
    UniqueConstraint("user_id", "name", name="uq_strategy_user_name"),
    Index("ix_strategies_user", "user_id"),
)

strategy_versions = Table(
    "strategy_versions",
    app_metadata,
    Column(
        "strategy_id",
        String(32),
        ForeignKey("strategies.strategy_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("version", Integer, primary_key=True),
    Column("author_user_id", String(32), nullable=False),
    Column("created_at", DateTime, nullable=False),
    #: Canonical JSON of rules / params. Canonical means sorted keys and no
    #: insignificant whitespace, so the hash is stable across processes.
    Column("definition", Text, nullable=False),
    #: sha256 of `definition`. Two identical versions are a mistake, not a new
    #: version, and are rejected rather than stored twice.
    Column("definition_hash", String(64), nullable=False),
    Column("change_note", Text),
    Column("is_deployed", Boolean, nullable=False, default=False),
    Index("ix_strategy_versions_hash", "definition_hash"),
    # Immutability is enforced by the absence of an UPDATE path in the repository
    # and by this primary key, not by a trigger. A rule that lives only in a
    # convention is a rule that will be broken.
)

backtest_runs = Table(
    "backtest_runs",
    app_metadata,
    Column("run_id", String(32), primary_key=True),
    Column("user_id", String(32), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
    Column("strategy_id", String(32)),
    Column("strategy_version", Integer),
    #: The registry key for a run launched against a built-in strategy. At least
    #: one of `engine_key` / `(strategy_id, strategy_version)` is always set.
    Column("engine_key", String(64)),
    Column("created_at", DateTime, nullable=False),
    Column("started_at", DateTime),
    Column("finished_at", DateTime),
    #: QUEUED | RUNNING | COMPLETED | FAILED | CANCELLED
    Column("status", String(12), nullable=False, default="QUEUED"),
    Column("progress", Float, nullable=False, default=0.0),
    #: JSON: dates, symbols, capital, costs. Enough to reproduce the run.
    Column("config", Text, nullable=False),
    #: sha256 of the price data used. A metric without this is not reproducible.
    Column("data_fingerprint", String(64)),
    #: JSON summary metrics, written once on completion.
    Column("metrics", Text),
    Column("error", Text),
    Index("ix_backtest_user_created", "user_id", "created_at"),
    Index("ix_backtest_status", "status"),
)

backtest_trades = Table(
    "backtest_trades",
    app_metadata,
    # Auto-increment surrogate: a round trip has no natural single-column key
    # (the same symbol can enter twice at the same timestamp on different
    # rebalances, and FIFO splits one fill across several lots).
    Column("trade_id", Integer, primary_key=True, autoincrement=True),
    Column(
        "run_id",
        String(32),
        ForeignKey("backtest_runs.run_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("seq", Integer, nullable=False),
    Column("symbol", String(64), nullable=False),
    #: LONG | SHORT. Stored explicitly rather than inferred from quantity sign,
    #: because a closed short has a positive exit delta and no sign to read.
    Column("direction", String(6), nullable=False),
    Column("quantity", Float, nullable=False),
    Column("entry_ts", DateTime, nullable=False),
    Column("entry_price", Float, nullable=False),
    Column("exit_ts", DateTime),
    Column("exit_price", Float),
    Column("gross_pnl", Float, nullable=False, default=0.0),
    Column("commission", Float, nullable=False, default=0.0),
    Column("net_pnl", Float, nullable=False, default=0.0),
    Column("return_pct", Float),
    Column("duration_days", Float),
    #: Why it was exited: stop_loss | take_profit | trailing_stop | signal | end_of_test
    Column("exit_reason", String(24)),
    #: The signal conditions that caused the entry, as prose. This is the field
    #: that answers "why did this trade happen" a year later, and the one the
    #: user asked for explicitly.
    Column("signal_reason", Text),
    #: Strategy identity stamped per trade as well as per run: a trade row is
    #: read on its own, and "which version produced this?" must not require a
    #: join that the reader might not think to make.
    Column("strategy_id", String(32)),
    Column("strategy_version", Integer),
    Index("ix_bt_trades_run", "run_id", "seq"),
    Index("ix_bt_trades_symbol", "symbol"),
)

backtest_curves = Table(
    "backtest_curves",
    app_metadata,
    Column("curve_id", Integer, primary_key=True, autoincrement=True),
    Column(
        "run_id",
        String(32),
        ForeignKey("backtest_runs.run_id", ondelete="CASCADE"),
        nullable=False,
    ),
    #: equity | drawdown | exposure | cash. One row set per kind per run.
    Column("kind", String(16), nullable=False),
    Column("seq", Integer, nullable=False),
    Column("ts", DateTime, nullable=False),
    Column("value", Float, nullable=False),
    Index("ix_bt_curves_run", "run_id", "kind", "seq"),
)

backtest_monthly = Table(
    "backtest_monthly",
    app_metadata,
    Column("row_id", Integer, primary_key=True, autoincrement=True),
    Column(
        "run_id",
        String(32),
        ForeignKey("backtest_runs.run_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("year", Integer, nullable=False),
    Column("month", Integer, nullable=False),
    #: Percent return for the month. NULL means the run did not cover it, which
    #: is different from 0.0% and must not be rendered as a flat month.
    Column("return_pct", Float),
    Index("ix_bt_monthly_run", "run_id", "year", "month"),
)
