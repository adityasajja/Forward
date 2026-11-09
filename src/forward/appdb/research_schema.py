"""Research schema tables."""

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
)

from forward.appdb.metadata import app_metadata

# ===========================================================================
# Learning tier — observations & research findings
# ===========================================================================
learning_observations = Table(
    "learning_observations",
    app_metadata,
    Column("observation_id", String(36), primary_key=True),
    Column("strategy_id", String(32), nullable=False),
    Column("strategy_version", Integer, nullable=True),
    Column("date", String(10), nullable=False),  # YYYY-MM-DD
    Column("created_at", DateTime, nullable=False),
    Column("metric", String(64), nullable=False),
    Column("condition_bucket", String(128), nullable=False),  # e.g. "rsi_bucket:low" or "rvol_bucket:>2.0"
    Column("sample_size", Integer, nullable=False),
    Column("statistical_result", Text, nullable=False),  # JSON dict with win_rate, mean, median, etc.
    Column("evidence_class", String(32), nullable=False),  # PAPER_FORWARD, BACKTEST, IN_SAMPLE, LIVE_FORWARD
    Column("confidence", Float, nullable=True),
    Column("source_trades", Text, nullable=True),  # JSON list of trade IDs
    Index("ix_learning_obs_strat", "strategy_id", "strategy_version"),
    Index("ix_learning_obs_date", "date"),
    Index("ix_learning_obs_evidence", "evidence_class"),
)

optimization_recommendations = Table(
    "optimization_recommendations",
    app_metadata,
    Column("recommendation_id", String(36), primary_key=True),
    Column(
        "strategy_id",
        String(32),
        ForeignKey("strategies.strategy_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("source_strategy_version", Integer, nullable=False),
    Column("target_strategy_version", Integer, nullable=True),
    Column("parameter", String(64), nullable=False),
    Column("current_value", Float, nullable=False),
    Column("proposed_value", Float, nullable=False),
    Column("reason", Text, nullable=False),
    Column("source_observations", Text, nullable=False),  # JSON list of observation details
    Column("sample_size", Integer, nullable=False),
    Column("baseline_metrics", Text, nullable=False),  # JSON dict
    Column("candidate_metrics", Text, nullable=False),  # JSON dict
    Column("walk_forward_metrics", Text, nullable=False),  # JSON dict
    Column("robustness_results", Text, nullable=False),  # JSON dict
    Column("confidence", String(16), nullable=True),
    Column("status", String(16), nullable=False, default="PROPOSED"),
    Column("rejection_reason", Text, nullable=True),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    Column("reviewed_by", String(64), nullable=True),
    Column("reviewed_at", DateTime, nullable=True),
    Index("ix_opt_rec_strategy", "strategy_id", "source_strategy_version"),
    Index("ix_opt_rec_status", "status"),
    Index("ix_opt_rec_created", "created_at"),
)

strategy_experiments = Table(
    "strategy_experiments",
    app_metadata,
    Column("experiment_id", String(36), primary_key=True),
    Column(
        "strategy_id",
        String(32),
        ForeignKey("strategies.strategy_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("source_version", Integer, nullable=False),
    Column("target_version", Integer, nullable=True),
    Column(
        "recommendation_id",
        String(36),
        ForeignKey("optimization_recommendations.recommendation_id", ondelete="SET NULL"),
        nullable=True,
    ),
    Column("creator_user_id", String(64), nullable=False),
    Column("name", String(128), nullable=False),
    Column("reason", Text, nullable=False),
    Column("parameter_changes", Text, nullable=False),  # JSON dict of changes
    Column("baseline_definition", Text, nullable=False),  # JSON dict
    Column("candidate_definition", Text, nullable=False),  # JSON dict
    Column("status", String(20), nullable=False, default="CREATED"),
    Column("rejection_reason", Text, nullable=True),
    Column("results", Text, nullable=True),  # JSON dict containing metrics comparison, curves, distributions, regimes
    Column("explanation", Text, nullable=True),  # JSON dict: what changed, why proposed, what experiment found
    Column("error", Text, nullable=True),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    Column("reviewed_by", String(64), nullable=True),
    Column("reviewed_at", DateTime, nullable=True),
    Index("ix_exp_strategy", "strategy_id", "source_version"),
    Index("ix_exp_status", "status"),
    Index("ix_exp_created", "created_at"),
)
