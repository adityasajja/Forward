"""Trade schema tables."""

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

trade_journal = Table(
    "trade_journal",
    app_metadata,
    Column("trade_id", String(32), primary_key=True),
    Column("user_id", String(32), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
    Column("deployment_id", String(32)),
    Column("strategy_id", String(32)),
    Column("strategy_version", Integer),
    Column("symbol", String(64), nullable=False),
    Column("asset_class", String(16), nullable=False, default="EQUITY"),
    Column("side", String(4), nullable=False),
    Column("quantity", Float, nullable=False),
    Column("entry_ts", DateTime, nullable=False),
    Column("exit_ts", DateTime),
    Column("entry_price", Float, nullable=False),
    Column("exit_price", Float),
    Column("gross_pnl", Float),
    Column("net_pnl", Float),
    #: Stored rather than derived: both need the intra-trade path, which the
    #: order-event log does not preserve once a position is netted.
    Column("mfe", Float),
    Column("mae", Float),
    Column("duration_sec", Integer),
    Column("regime", String(24)),
    Column("signal_reason", Text),
    #: Why the trade ended — the closing order's own reason ("stop_loss",
    #: "target", "time_stop"). A property of the decision rather than of the
    #: price path, so it cannot be recovered from the fills: it is recorded or
    #: it is absent. Existing databases require an explicit migration.
    Column("exit_reason", String(32)),
    #: The closing rule's full sentence ("stop_loss: down 10.2% against an average of 1,234.56"). The
    #: short ``exit_reason`` is just its code, so trades group by rule instead of by wording.
    Column("exit_detail", Text),
    Column("slippage_bps", Float),
    #: How independent this record is: ``forward`` when the opening order was
    #: raised live — the OMS stamps the ``NEW`` event with the wall-clock instant
    #: it created the order, and the journal copies that verdict here — and
    #: ``in_sample`` otherwise. A row that cannot demonstrate it was written
    #: before its outcome is graded ``in_sample``, because the alternative is an
    #: in-sample measurement wearing a forward label. Read by
    #: ``forward.services.learning``, which is the only consumer that may treat the
    #: column as evidence. Existing databases require an explicit migration.
    Column("evidence_grade", String(16)),
    Column("notes", Text),
    Column("created_at", DateTime, nullable=False),
    Index("ix_journal_user_exit", "user_id", "exit_ts"),
    Index("ix_journal_strategy", "strategy_id", "strategy_version"),
    Index("ix_journal_symbol", "symbol"),
)

#: The post-trade attribution of one closed trade — the nine-branch object from
#: :mod:`forward.analytics.attribution`, persisted.
#:
#: **Why a separate table rather than more columns on ``trade_journal``.** The
#: journal answers *what happened*: a position opened, it closed, it made this
#: much. Attribution answers *why, and which part of the chain did it*, and it is
#: a different kind of record in three ways that matter:
#:
#: 1. **It is derived, not primary.** A journal row is written by the fold and is
#:    true the instant it exists. An attribution row is computed from the journal,
#:    the order-event log and the price cache, so it can be absent (not yet
#:    computed) or superseded (a late fill arrived). Putting it on the journal
#:    would make *"this trade has no net P&L"* and *"this trade has not been
#:    attributed"* the same sentence.
#: 2. **It has a version and a provenance of its own.** ``computed_at`` and
#:    ``input_fingerprint`` say when it was derived and from what; a re-run with
#:    an unchanged fingerprint is a no-op, and a re-run with a changed one is a
#:    replacement. Neither concept exists on the journal.
#: 3. **It is keyed one-per-trade by construction.** ``trade_id`` is the primary
#:    key, which is what makes attribution **idempotent at the database level** —
#:    a second computation cannot create a second row, whatever the caller does.
#:
#: The attribution row never *generates* evidence. ``evidence_grade`` here is a
#: **copy of the journal's verdict**, taken so a query can filter on it without a
#: join; the service reads the journal's column and refuses to accept a grade
#: from any other source. A row whose copied grade disagreed with its journal row
#: would be a second opinion about provenance, and provenance is not a matter of
#: opinion.
trade_attributions = Table(
    "trade_attributions",
    app_metadata,
    Column("trade_id", String(32), primary_key=True),
    Column(
        "user_id",
        String(32),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("deployment_id", String(32)),
    Column("symbol", String(64), nullable=False),
    Column("side", String(4), nullable=False),
    #: BACKTEST | PAPER | LIVE — the venue the trade came from.
    Column("source", String(16), nullable=False),
    #: forward | in_sample. **Copied from ``trade_journal.evidence_grade``**, never
    #: computed here. Duplicated into this table so a forward-only query does not
    #: have to join; the service is the only writer and takes the value from the
    #: journal. See the module docstring of ``forward.services.attribution``.
    Column("evidence_grade", String(16)),
    #: PAPER_FORWARD | LIVE_FORWARD | IN_SAMPLE | BACKTEST — the class the grade
    #: was derived from, kept so a reader can see *why* the grade is what it is.
    Column("evidence_class", String(16)),
    #: True when the fills were produced by a matching model rather than reported
    #: by a broker. Backtest and paper are both simulated; live is not. Carried so
    #: a comparison can filter simulated from real without consulting a second
    #: table.
    Column("simulated", Boolean, nullable=False, default=True),

    # ── the nine branches, frozen as JSON ──────────────────────────────────
    #: The full attribution object: the TRADE tree, the excursion set, the
    #: reason codes and their stated basis, the missing fields, and the
    #: thresholds used. Stored whole rather than exploded into ~80 columns
    #: because the tree's *shape* is the deliverable and a reader wants it
    #: intact; the fields the learning axes actually slice on are lifted into
    #: their own columns below, and those are the only ones a query needs.
    Column("attribution", Text, nullable=False),
    #: The codes, flattened and comma-joined, so ``LIKE`` can find a code without
    #: parsing every blob. A denormalisation with a single writer, which is the
    #: only kind that is safe.
    Column("reason_codes", Text),
    Column("missing_fields", Text),

    # ── lifted columns: what the learning axes slice on ────────────────────
    Column("entry_quality", String(16)),
    Column("execution_quality", String(24)),
    Column("mfe_pct", Float),
    Column("mae_pct", Float),
    Column("mfe_amount", Float),
    Column("mae_amount", Float),
    Column("mfe_over_risk", Float),
    Column("realized_over_risk", Float),
    Column("capture_efficiency_pct", Float),
    Column("entry_slippage_bps", Float),
    Column("exit_slippage_bps", Float),
    Column("total_slippage_bps", Float),
    Column("transaction_costs", Float),
    Column("cost_pct", Float),
    Column("signal_to_order_sec", Float),
    Column("order_to_fill_sec", Float),
    Column("holding_sec", Integer),
    Column("partial_fill", Boolean, nullable=False, default=False),
    Column("fill_ratio", Float),
    Column("sizing_method", String(32)),
    Column("sizing_cap_reason", String(64)),
    Column("realized_risk_pct", Float),
    Column("planned_risk_amount", Float),
    Column("context_score", Integer),
    Column("context_class", String(24)),
    Column("market_regime", String(24)),
    Column("sector", String(64)),
    Column("sector_strength", Float),
    Column("stock_relative_strength", Float),
    Column("rvol", Float),
    Column("atr_pct", Float),
    Column("exit_reason", String(32)),

    # ── bookkeeping ────────────────────────────────────────────────────────
    #: Hash of everything the computation read. A re-run that produces the same
    #: fingerprint is a no-op rather than a rewrite, which is what makes
    #: "attribute every closed trade" safe to schedule: the second run over an
    #: unchanged book writes nothing.
    Column("input_fingerprint", String(64), nullable=False),
    Column("computed_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    Index("ix_attr_user_computed", "user_id", "computed_at"),
    Index("ix_attr_user_grade", "user_id", "evidence_grade"),
    Index("ix_attr_strategy", "symbol"),
    Index("ix_attr_regime", "market_regime"),
    Index("ix_attr_sector", "sector"),
)


# ===========================================================================
# Signal context (Context-Aware Signal Engine)
# ===========================================================================
# One row per enriched signal. Read-only from the signal engine's point of view:
# it is written after the signal fires and never retroactively changes. ``run_id``
# + ``trade_id`` link a backtest context to its trade row; ``order_id`` /
# ``signal_id`` link a live/paper context to the order it produced.
signal_contexts = Table(
    "signal_contexts",
    app_metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", String(32), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
    #: Stable identifier of the signal, established by whoever raised it. For a
    #: paper deployment this is the bar key (``SYMBOL:YYYY-MM-DD``); for a
    #: backtest run it is ``RUN_ID:SEQ``.
    Column("signal_id", String(64), nullable=False),
    Column("strategy_id", String(32)),
    Column("strategy_version", Integer),
    Column("symbol", String(64), nullable=False),
    #: BUY | SELL
    Column("action", String(8), nullable=False),
    #: LIVE | PAPER | BACKTEST
    Column("signal_source", String(16), nullable=False),
    Column("signal_ts", String(40), nullable=False),
    #: The SignalContextModelConfig version that produced this record. A change
    #: of scoring weights must not reinterpret existing rows.
    Column("context_model_version", String(24), nullable=False),
    #: STRONG_CONTEXT | NEUTRAL_CONTEXT | WEAK_CONTEXT | INSUFFICIENT_DATA
    Column("context_class", String(24), nullable=False),
    Column("context_score", Integer, nullable=False, default=0),
    Column("max_possible_score", Integer, nullable=False, default=0),
    Column("has_insufficient_data", Boolean, nullable=False, default=False),
    #: Linkage back to the source of the signal, for outcome joins.
    Column("run_id", String(32)),
    Column("trade_id", Integer),
    Column("order_id", String(32)),
    #: JSON snapshots — kept as text so the record is self-describing and a new
    #: scoring version can reclassify old rows without more columns.
    Column("market_context", Text),
    Column("sector_context", Text),
    Column("stock_context", Text),
    Column("score_breakdown", Text),
    Column("missing_fields", Text),
    Column("benchmark_provenance", Text),
    Column("created_at", DateTime, nullable=False),
    #: One context per signal. Live/paper enrichment is insert-once: a re-fire of
    #: the same bar must dedupe rather than double-count in analytics. Backtest
    #: rows are replaced wholesale per run, so they never collide on this key.
    UniqueConstraint("user_id", "signal_id", name="uq_signal_context_user_signal"),
    Index("ix_signal_ctx_user_ts", "user_id", "signal_ts"),
    Index("ix_signal_ctx_symbol", "symbol"),
    Index("ix_signal_ctx_source", "signal_source"),
    Index("ix_signal_ctx_run_trade", "run_id", "trade_id"),
    Index("ix_signal_ctx_signal_id", "signal_id"),
)
