"""SQLAlchemy tables for options datasets and runs."""

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Table, Text

from forward.appdb.metadata import app_metadata

options_datasets = Table(
    "options_datasets",
    app_metadata,
    Column("dataset_id", String(32), primary_key=True),
    Column(
        "user_id",
        String(32),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("name", String(128), nullable=False),
    Column("bars_path", Text, nullable=False),
    Column("instruments_path", Text, nullable=False),
    Column("bar_count", Integer, nullable=False),
    Column("symbol_count", Integer, nullable=False),
    Column("created_at", DateTime, nullable=False),
    Index("ix_options_datasets_user_created", "user_id", "created_at"),
)

options_backtest_runs = Table(
    "options_backtest_runs",
    app_metadata,
    Column("run_id", String(32), primary_key=True),
    Column(
        "user_id",
        String(32),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "strategy_id",
        String(32),
        ForeignKey("strategies.strategy_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("strategy_version", Integer, nullable=False),
    Column(
        "dataset_id",
        String(32),
        ForeignKey("options_datasets.dataset_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("status", String(16), nullable=False),
    Column("config", Text, nullable=False),
    Column("result", Text),
    Column("error", Text),
    Column("created_at", DateTime, nullable=False),
    Column("finished_at", DateTime),
    Index("ix_options_backtests_user_created", "user_id", "created_at"),
    Index("ix_options_backtests_status", "status"),
)

options_forward_runs = Table(
    "options_forward_runs",
    app_metadata,
    Column("run_id", String(32), primary_key=True),
    Column(
        "user_id",
        String(32),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "strategy_id",
        String(32),
        ForeignKey("strategies.strategy_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("strategy_version", Integer, nullable=False),
    Column("status", String(16), nullable=False),
    Column("config", Text, nullable=False),
    Column("snapshot", Text),
    Column("error", Text),
    Column("created_at", DateTime, nullable=False),
    Column("finished_at", DateTime),
    Index("ix_options_forward_user_created", "user_id", "created_at"),
    Index("ix_options_forward_status", "status"),
)
