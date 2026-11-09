"""Learning evidence rows and their dataset builder."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from loguru import logger

from forward.config.settings import get_settings
from forward.core.historical_market_data_ports import CachedMarketFramesPort
from forward.core.learning_evidence import (
    CLASS_BACKTEST,
    CLASS_IN_SAMPLE,
    GRADE_FORWARD,
    GRADE_IN_SAMPLE,
    NOTE_BACKTEST_IN_SAMPLE,
    NOTE_FORWARD_STAMPED,
    NOTE_MARKED_IN_SAMPLE,
    NOTE_NO_STAMP,
    SOURCE_BACKTEST,
    SOURCE_LIVE,
    SOURCE_PAPER,
    SOURCES,
    forward_class,
    is_forward_grade,
)
from forward.core.learning_ports import LearningArtifactsPort, LearningStoragePort
from forward.core.portfolio_ports import PortfolioStoragePort
from forward.research import learning_paper as paper
from forward.research.learning_attribution import extract_reason
from forward.research.learning_enrich import (
    benchmark_provenance_at,
    bucket_atr,
    bucket_relative_volume,
    bucket_rsi,
    label_regime,
    median_or_none,
    metrics_at,
    relative_strength_at,
    time_of_day,
    trend_at,
)
from forward.research.learning_missing_features import missing_feature_registry
from forward.services.learning_dataset_attribution import attribution_columns
from forward.services.learning_dataset_model import LearningDataset as _LearningDataset


class LearningError(Exception):
    """A learning read that cannot be served."""

    def __init__(self, message: str, *, code: str = "learning_error", status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


class LearningDatasetBuilder:
    """Assembles the dataset from the evidence that exists.

    Reads, in order of authority:

    * ``backtest_trades`` joined to its ``backtest_runs`` row — BACKTEST source.

    * ``trade_journal`` — PAPER or LIVE, decided by the deployment's mode where

      one can be found, and otherwise left as PAPER with a warning, because

      guessing LIVE when it is actually paper would overstate how much real

      money the evidence represents.

    * the forward paper ledger under ``data/paper_momentum`` — PAPER, and the

      only source in this project that holds a record written *before* the

      outcome it describes. Read through

      :mod:`forward.research.learning_paper`, which keeps its in-sample backfilled

      weeks graded separately from its forward ones.

    Everything else is derived or marked missing.

    """

    def __init__(
        self,
        *,
        storage: LearningStoragePort,
        portfolio_storage: PortfolioStoragePort,
        market_history: CachedMarketFramesPort,
        artifacts: LearningArtifactsPort,
    ) -> None:
        """Create a learning dataset builder with explicit portfolio storage.

        Args:

            storage: Port used to read stored learning evidence.

            portfolio_storage: Port used to read portfolio context for each trade.

            market_history: Port used to load daily bars and benchmark frames.

            artifacts: Port used for learning input and report files.

        """
        self._portfolio_storage = portfolio_storage
        self._storage = storage
        self._market_history = market_history
        self._artifacts = artifacts
        self._sector_cache: dict[str, str] | None = None

    def _load_ledger(self) -> paper.PaperLedger:
        return self._artifacts.paper_ledger()

    # ------------------------------------------------------------------
    def build(
        self,
        *,
        user_id: str | None = None,
        limit: int = get_settings().learning_dataset_max_rows,
    ) -> _LearningDataset:
        """The whole dataset. Empty input yields a valid empty dataset."""
        warnings: list[str] = []
        now = datetime.now(UTC)
        runs = self._completed_runs(user_id=user_id, limit=limit, warnings=warnings)
        backtest_rows = self._backtest_rows(runs, warnings=warnings)
        journal_rows = self._journal_rows(user_id=user_id, limit=limit, warnings=warnings)
        if user_id is None:
            ledger = self._load_ledger()
            paper_rows = paper.build_rows(ledger)
            warnings.extend(ledger.warnings)
            ledger_summary = ledger.summary()
        else:
            paper_rows = []
            ledger_summary = {}
            warnings.append(
                "the file-based paper ledger has no account owner and is excluded "
                "from this account-scoped dataset"
            )
        sectors = self._sector_table()
        frames = self._load_frames(
            {row["symbol"] for row in (*backtest_rows, *journal_rows, *paper_rows)}
        )
        # The benchmark is fetched once, not per trade: it is the same series for
        # every row and re-reading it inside the loop would dominate the run.
        benchmarks = self._benchmark_frames()
        combined = [*backtest_rows, *journal_rows, *paper_rows]
        # Enrich once, then derive the sample-relative bucket from those same rows.
        # The bucket needs the median of all ATR values, so it is assigned after
        # the complete pass rather than by enriching every row twice.
        rows = self._enrich_all(combined, frames, sectors=sectors, benchmarks=benchmarks)
        atr_median = median_or_none([row.get("_atr_pct") for row in rows])
        for row in rows:
            row["forward_bucket"] = bucket_atr(row.get("atr_pct"), atr_median)
        source_counts = {
            source: sum(1 for row in rows if row.get("source") == source) for source in SOURCES
        }
        missing = missing_feature_registry(rows)
        if not rows:
            warnings.append(
                "no trades were found in the account-scoped backtest and journal "
                "records; the dataset is correctly empty rather than "
                "unavailable"
            )
        dataset = _LearningDataset(
            rows=rows,
            missing_features=missing,
            generated_at=now,
            warnings=warnings,
            source_counts=source_counts,
            ledger=ledger_summary,
        )
        logger.info(
            "learning dataset: {} observations ({} backtest, {} paper/live, {} forward), "
            "missing features: {}",
            len(rows),
            source_counts.get(SOURCE_BACKTEST, 0),
            source_counts.get(SOURCE_PAPER, 0) + source_counts.get(SOURCE_LIVE, 0),
            sum(1 for row in rows if row.get("is_forward")),
            ", ".join(sorted(missing)) or "none",
        )
        return dataset

    # ------------------------------------------------------------------
    def _completed_runs(
        self, *, user_id: str | None, limit: int, warnings: list[str]
    ) -> dict[str, dict[str, Any]]:
        """Completed runs keyed by id.

        Only COMPLETED runs are read. A FAILED run has no trade rows by

        construction (``BacktestService._fail`` deletes partial artefacts), and

        reading a RUNNING run's half-written trades would put a partially

        realised equity path into the statistics as though it were a result.

        """
        try:
            rows = self._storage.completed_backtest_runs(user_id, limit)
            return {row["run_id"]: row for row in rows}
        except Exception as exc:  # noqa: BLE001 — a cold database is not a crash
            warnings.append(f"backtest_runs could not be read: {type(exc).__name__}")
            logger.warning("learning: could not read backtest runs: {}", exc)
            return {}

    def _backtest_rows(
        self, runs: dict[str, dict[str, Any]], *, warnings: list[str]
    ) -> list[dict[str, Any]]:
        if not runs:
            return []
        rows: list[dict[str, Any]] = []
        try:
            trades = self._storage.backtest_trades(list(runs))
            rows.extend(self._backtest_row(raw, runs.get(raw["run_id"], {})) for raw in trades)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"backtest_trades could not be read: {type(exc).__name__}")
            logger.warning("learning: could not read backtest trades: {}", exc)
        return rows

    @staticmethod
    def _backtest_row(raw: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
        reason = raw.get("signal_reason")
        extracted = extract_reason(reason)
        direction = str(raw.get("direction") or "LONG").upper()
        missing: list[str] = []
        # The two fields the source table structurally cannot carry. Marked here
        # rather than upstream, because "the journal has it and this does not" is
        # a property of the *source*, and a reader comparing two rows needs to
        # see which source they came from to understand the blank.
        missing.extend(["mfe", "mae", "slippage_bps"])
        if not reason:
            missing.append("signal_reason")
        if extracted.volume_multiple is None:
            missing.append("relative_volume_from_reason")
        return {
            "source": SOURCE_BACKTEST,
            # A backtest is in-sample by construction: the rule was selected on
            # the history it is being measured over. That does not make the
            # number wrong, but it does mean a backtest row can never be counted
            # as independent confirmation — which is what this label says.
            "evidence_class": CLASS_BACKTEST,
            "evidence_grade": GRADE_IN_SAMPLE,
            "evidence_note": NOTE_BACKTEST_IN_SAMPLE,
            "is_forward": False,
            "source_ref": raw.get("run_id"),
            "trade_ref": f"{raw.get('run_id')}#{raw.get('seq')}",
            # The backtest signal id is exact (``RUN_ID:SEQ``), so linkage needs
            # no fuzzy match. Recorded backtest contexts stay with the
            # signal-context service; only the forward path is joined here.
            "signal_id": f"{raw.get('run_id')}:{raw.get('seq')}",
            "opening_order_id": None,
            "context_score": None,
            "context_model_version": None,
            "context_class": None,
            "strategy_key": run.get("engine_key"),
            "strategy_id": raw.get("strategy_id") or run.get("strategy_id"),
            "strategy_version": raw.get("strategy_version") or run.get("strategy_version"),
            "engine_key": run.get("engine_key"),
            "symbol": _canonical(raw.get("symbol")),
            "sector": None,  # filled by the enrichment pass
            "direction": "LONG" if direction.startswith("L") else "SHORT",
            "entry_ts": raw.get("entry_ts"),
            "exit_ts": raw.get("exit_ts"),
            "duration_days": raw.get("duration_days"),
            "day_of_week": _dow(raw.get("entry_ts")),
            "time_of_day": time_of_day(raw.get("entry_ts")),
            "quantity": raw.get("quantity"),
            "entry_price": raw.get("entry_price"),
            "exit_price": raw.get("exit_price"),
            "gross_pnl": raw.get("gross_pnl"),
            "commission": raw.get("commission"),
            "net_pnl": raw.get("net_pnl"),
            "return_pct": raw.get("return_pct"),
            "exit_reason": raw.get("exit_reason"),
            "mfe": None,
            "mae": None,
            "slippage_bps": None,
            "signal_reason": reason,
            "setup": extracted.setup if reason else None,
            "rsi": extracted.rsi,
            "sma_fast": extracted.sma_fast,
            "sma_slow": extracted.sma_slow,
            "sma_long": extracted.sma_long,
            "prior_high": extracted.prior_high,
            "signal_volume_multiple": extracted.volume_multiple,
            "breakout_proximity_pct": extracted.proximity_pct,
            "portfolio_exposure_at_entry": None,
            "strategy_allocation": None,
            "sector_exposure_pct": None,
            "position_concentration": None,
            "risk_per_trade": None,
            "position_size": raw.get("quantity"),
            "position_value": (
                float(raw["quantity"]) * float(raw["entry_price"])
                if raw.get("quantity") and raw.get("entry_price")
                else None
            ),
            "stop_distance": None,
            "forward_based_risk": None,
            "_missing": missing,
        }

    def _journal_rows(
        self, *, user_id: str | None, limit: int, warnings: list[str]
    ) -> list[dict[str, Any]]:
        modes = self._deployment_modes(user_id, warnings)
        rows: list[dict[str, Any]] = []
        try:
            journal_rows = self._storage.journal_rows(user_id, limit)
            rows.extend(self._journal_row(raw, modes, warnings) for raw in journal_rows)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"trade_journal could not be read: {type(exc).__name__}")
            logger.warning("learning: could not read trade journal: {}", exc)
        self._attach_recorded_context(rows, user_id=user_id, warnings=warnings)
        self._attach_attribution(rows, user_id=user_id, warnings=warnings)
        return rows

    def _attach_attribution(
        self, rows: list[dict[str, Any]], *, user_id: str | None, warnings: list[str]
    ) -> None:
        """Join each journal row to its post-trade attribution, where one exists.

        Read from ``trade_attributions`` keyed by ``trade_id`` — an exact join,

        unlike the signal match, because both tables are keyed on the same episode.

        **This is a read, never a computation.** Nothing here attributes a trade;

        the sweep in :mod:`forward.services.attribution` does that, on its own

        schedule. The consequence is that the dataset can be built against a book

        whose attribution is incomplete, and the honest response is to leave those

        columns ``None`` and register the reason — not to compute an attribution

        on the fly, which would let the dataset builder become a second

        implementation of the arithmetic and would let a build take minutes.

        **Attribution is a read of the book, not of a strategy.** The columns

        joined here are outcome and execution measurements. They cannot make a

        row forward, cannot change its grade, and are joined after the grade was

        resolved — so a mis-join cannot promote an in-sample trade into evidence.

        Mutates ``rows`` in place. Any failure degrades to unattributed rows.

        """
        if not rows:
            return
        trade_ids = [
            str(row.get("trade_ref"))
            for row in rows
            if row.get("source") in (SOURCE_PAPER, SOURCE_LIVE) and row.get("trade_ref")
        ]
        if not trade_ids:
            return
        try:
            attributions = self._storage.trade_attributions(user_id, trade_ids) if user_id else {}
        except Exception as exc:  # noqa: BLE001
            warnings.append(
                "trade attributions could not be read; journal rows carry no "
                f"attribution fields ({type(exc).__name__})"
            )
            logger.warning("learning: could not read attributions: {}", exc)
            return
        for row in rows:
            if row.get("source") not in (SOURCE_PAPER, SOURCE_LIVE):
                # A backtest trade and a paper-ledger row have no journal episode
                # and therefore no attribution row. The columns stay None and the
                # reason is registered by the missing-feature pass, which is what
                # keeps "not applicable to this source" distinguishable from
                # "not computed yet".
                if row.get("source") == SOURCE_BACKTEST:
                    self._mark_missing(row, "attribution_entry_quality", "backtest_source")
                else:
                    self._mark_missing(row, "attribution_entry_quality", "ledger_source")
                continue
            record = attributions.get(str(row.get("trade_ref")))
            if record is None:
                self._mark_missing(row, "attribution_entry_quality", "not_attributed")
                continue
            flat = attribution_columns(record)
            row.update(flat)
            # The attribution reports fields the row did not have, and a row that
            # gains a feature must lose its "missing" entry for it — otherwise the
            # dataset would claim to be missing a number it is carrying.
            row["_missing"] = [
                name for name in (row.get("_missing") or []) if not name.startswith("attribution_")
            ]
            row["missing_features"] = sorted(set(row["_missing"]))

    @staticmethod
    def _mark_missing(row: dict[str, Any], feature: str, reason: str) -> None:
        missing = list(row.get("_missing") or [])
        if feature not in missing:
            missing.append(feature)
        row["_missing"] = sorted(set(missing))
        row["missing_features"] = sorted(set(missing))
        row.setdefault("_attribution_missing_reason", reason)

    def _attach_recorded_context(
        self, rows: list[dict[str, Any]], *, user_id: str | None, warnings: list[str]
    ) -> None:
        """Link journal rows back to their signal, opening order and context.

        A journal episode records no ``signal_id`` — the order that raised it

        does. So each row is matched to the earliest order with the same

        strategy, symbol and direction whose creation falls within

        ``SIGNAL_MATCH_WINDOW_SEC`` of the entry: the same rule

        ``SignalContextService._match_episode`` applies from the context side,

        so the two directions resolve identically. The recorded context is then

        read by that order's ``signal_id`` and kept as recorded.

        Mutates ``rows`` in place. Anything unresolvable stays ``None`` — an

        unlinked row is reported by data-quality monitoring, never invented.

        A failure here degrades to unlinked rows, never to a failed build.

        """
        from forward.research.learning_readiness import SIGNAL_MATCH_WINDOW_SEC

        if not rows:
            return
        try:
            order_rows, context_rows = self._storage.recorded_signal_contexts(user_id)
        except Exception as exc:  # noqa: BLE001
            warnings.append(
                "recorded signal contexts could not be read; journal rows stay "
                f"unlinked ({type(exc).__name__})"
            )
            logger.warning("learning: could not read recorded contexts: {}", exc)
            return
        contexts = {str(r["signal_id"]): r for r in context_rows if r.get("signal_id")}
        buckets: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for order in order_rows:
            if not order.get("strategy_id"):
                continue
            key = (
                str(order["strategy_id"]),
                str(order.get("symbol") or "").upper(),
                "SHORT" if str(order.get("side") or "").upper() in ("SELL", "S") else "LONG",
            )
            buckets.setdefault(key, []).append(dict(order))
        matched_order_ids: set[str] = set()
        rows_by_entry_time = sorted(
            rows,
            key=lambda row: _timestamp_sort_key(row.get("entry_ts")),
        )
        for row in rows_by_entry_time:
            if not row.get("strategy_id") or row.get("entry_ts") is None:
                continue
            key = (
                str(row["strategy_id"]),
                str(row.get("symbol") or "").upper(),
                str(row.get("direction") or "LONG").upper(),
            )
            candidates = buckets.get(key) or []
            best: dict[str, Any] | None = None
            for order in candidates:
                order_id = str(order["order_id"])
                if order_id in matched_order_ids:
                    continue
                row_deployment_id = row.get("source_ref")
                order_deployment_id = order.get("deployment_id")
                if (
                    row_deployment_id is not None
                    and order_deployment_id is not None
                    and str(row_deployment_id) != str(order_deployment_id)
                ):
                    continue
                gap = _ts_gap_sec(row.get("entry_ts"), order.get("created_at"))
                if gap is None or gap > SIGNAL_MATCH_WINDOW_SEC:
                    continue
                best = order
                break  # orders are creation-ordered: the first hit is the entry
            if best is None:
                continue
            matched_order_ids.add(str(best["order_id"]))
            row["opening_order_id"] = best.get("order_id")
            row["signal_id"] = best.get("signal_id")
            context = contexts.get(str(best.get("signal_id") or ""))
            if context is not None:
                row["context_score"] = context.get("context_score")
                row["context_model_version"] = context.get("context_model_version")
                row["context_class"] = context.get("context_class")
        # Portfolio context replay at entry (exposure, allocation, sector %, concentration)
        try:
            from forward.services.portfolio import PortfolioService
            from forward.services.portfolio_context import snapshots_at_entry
            from forward.services.portfolio_models import sector_resolver

            port_service = PortfolioService(storage=self._portfolio_storage)
            fills = port_service.fills_for_user(user_id) if user_id else []
            capitals: dict[str, float] = {}
            if user_id:
                capitals = {
                    str(deployment["deployment_id"]): float(deployment.get("capital") or 0.0)
                    for deployment in port_service.deployments_for_user(user_id)
                }
            entries_for_replay = [
                {
                    "key": idx,
                    "ts": r.get("entry_ts"),
                    "deployment_id": r.get("source_ref"),
                    "symbol": r.get("symbol"),
                }
                for idx, r in enumerate(rows)
            ]
            snaps = snapshots_at_entry(
                entries_for_replay,
                fills,
                capitals,
                sector_resolver(self._artifacts.sector_table()),
            )
            for idx, r in enumerate(rows):
                snap = snaps.get(idx) or {}
                r["portfolio_exposure_at_entry"] = snap.get("exposure")
                r["strategy_allocation"] = snap.get("allocation")
                r["sector_exposure_pct"] = snap.get("sector_pct")
                r["position_concentration"] = snap.get("concentration")
        except Exception as exc:  # noqa: BLE001
            logger.debug("learning: portfolio replay skipped: {}", exc)

    def _deployment_modes(
        self, user_id: str | None, warnings: list[str]
    ) -> dict[str, str]:
        """``deployment_id -> mode``, so PAPER and LIVE can be told apart.

        The journal itself does not record whether a trade was paper or real

        money; the deployment does. Without this lookup every journal trade would

        be reported as PAPER, which would understate the live evidence — and the

        drift analysis is *entirely* about the difference between simulated and

        real, so getting this wrong would break the one comparison that matters.

        """
        try:
            return self._storage.deployment_modes(user_id)
        except Exception as exc:  # noqa: BLE001
            warnings.append(
                "deployment modes could not be read; journal trades are labelled "
                f"PAPER by default ({type(exc).__name__})"
            )
            return {}

    @staticmethod
    def _journal_row(
        raw: dict[str, Any], modes: dict[str, str], warnings: list[str]
    ) -> dict[str, Any]:
        deployment_id = raw.get("deployment_id")
        mode = modes.get(deployment_id or "", "")
        source = SOURCE_LIVE if mode in {"LIVE", "REAL"} else SOURCE_PAPER
        reason = raw.get("signal_reason")
        extracted = extract_reason(reason)
        side = str(raw.get("side") or "BUY").upper()
        direction = "LONG" if side == "BUY" else "SHORT"
        duration_days = None
        if raw.get("entry_ts") is not None and raw.get("exit_ts") is not None:
            delta = raw["exit_ts"] - raw["entry_ts"]
            duration_days = delta.total_seconds() / 86_400.0
        return_pct = None
        entry_price = raw.get("entry_price")
        exit_price = raw.get("exit_price")
        if entry_price and exit_price:
            sign = 1.0 if direction == "LONG" else -1.0
            return_pct = (float(exit_price) / float(entry_price) - 1.0) * 100.0 * sign
        missing: list[str] = []
        for name in ("mfe", "mae", "slippage_bps"):
            if raw.get(name) is None:
                missing.append(name)
        if not reason:
            missing.append("signal_reason")
        if extracted.volume_multiple is None:
            missing.append("relative_volume_from_reason")
        # Costs, recovered from the two figures the journal does record rather
        # than re-derived from a cost model. The journal closes a trade as
        # ``gross - commission``, so the difference *is* what was charged — and
        # recomputing it from today's rates would restate history whenever a
        # rate changed.
        gross = raw.get("gross_pnl")
        net = raw.get("net_pnl")
        commission = None
        if gross is not None and net is not None:
            commission = float(gross) - float(net)
        else:
            missing.append("commission")
        exit_reason = raw.get("exit_reason")
        if not exit_reason:
            missing.append("exit_reason")
        #: The provenance verdict the journal recorded, which it in turn read from
        #: the provenance stamp the OMS wrote on the opening order's ``NEW``
        #: event. This is the field that decides whether the row is evidence, so
        #: it is resolved once, here, and never re-derived downstream.
        grade, grade_note = _journal_grade(raw)
        forward = is_forward_grade(grade)
        return {
            "source": source,
            # Where the record came from. A paper trade recorded before its
            # outcome was known is ``PAPER_FORWARD``; a paper fill whose order
            # carries no execution-time stamp cannot demonstrate that, so it is
            # ``IN_SAMPLE``. Which of the two paper classes applies is decided by
            # the deployment's mode, not by the journal, which does not record it.
            "evidence_class": forward_class(source) if forward else CLASS_IN_SAMPLE,
            "evidence_grade": grade,
            "evidence_note": grade_note,
            "is_forward": forward,
            "source_ref": deployment_id,
            "trade_ref": raw.get("trade_id"),
            # Linkage to the raising signal, its opening order and its recorded
            # context. Resolved by the builder's ``_attach_recorded_context``
            # pass (same rule the signal-context service applies from the other
            # direction); ``None`` until then, and ``None`` forever when the
            # trade cannot be linked back.
            "signal_id": None,
            "opening_order_id": None,
            "context_score": None,
            "context_model_version": None,
            "context_class": None,
            "strategy_key": raw.get("strategy_id"),
            "strategy_id": raw.get("strategy_id"),
            "strategy_version": raw.get("strategy_version"),
            "engine_key": None,
            "symbol": _canonical(raw.get("symbol")),
            "sector": None,
            "direction": direction,
            "entry_ts": raw.get("entry_ts"),
            "exit_ts": raw.get("exit_ts"),
            "duration_days": duration_days,
            "day_of_week": _dow(raw.get("entry_ts")),
            "time_of_day": time_of_day(raw.get("entry_ts")),
            "quantity": raw.get("quantity"),
            "entry_price": entry_price,
            "exit_price": exit_price,
            "gross_pnl": gross,
            "commission": commission,
            "net_pnl": net,
            "return_pct": return_pct,
            # Why the trade ended, from the closing order's own reason. It is a
            # property of the decision, not of the price path, so it cannot be
            # recovered from the fills — it is recorded or it is absent.
            "exit_reason": exit_reason,
            "mfe": raw.get("mfe"),
            "mae": raw.get("mae"),
            "slippage_bps": raw.get("slippage_bps"),
            "signal_reason": reason,
            "setup": extracted.setup if reason else None,
            "rsi": extracted.rsi,
            "sma_fast": extracted.sma_fast,
            "sma_slow": extracted.sma_slow,
            "sma_long": extracted.sma_long,
            "prior_high": extracted.prior_high,
            "signal_volume_multiple": extracted.volume_multiple,
            "breakout_proximity_pct": extracted.proximity_pct,
            "portfolio_exposure_at_entry": None,
            "strategy_allocation": None,
            "sector_exposure_pct": None,
            "position_concentration": None,
            "risk_per_trade": None,
            "position_size": raw.get("quantity"),
            "position_value": (
                float(raw["quantity"]) * float(raw.get("entry_price") or 0.0)
                if raw.get("quantity") and raw.get("entry_price")
                else None
            ),
            "stop_distance": None,
            "forward_based_risk": None,
            # The journal stores a regime label from when the trade opened. It is
            # point-in-time by construction, which is exactly what the axis
            # needs, so it is preferred over a recomputed label.
            "_journal_regime": raw.get("regime"),
            "_missing": missing,
        }

    # ------------------------------------------------------------------
    def _enrich_all(
        self,
        rows: list[dict[str, Any]],
        frames: dict[str, Any],
        *,
        sectors: dict[str, str] | None = None,
        benchmarks: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Add entry-time context to every row. Pure-ish: returns new dicts.

        Called twice — once to find the ATR median, once to persist. The second

        call is the one whose output is kept, so a row's features never depend on

        the order the rows happened to be processed in.

        """
        out: list[dict[str, Any]] = []
        empty: Any = {}
        sectors = sectors if sectors is not None else empty
        benchmarks = benchmarks if benchmarks is not None else empty
        atr_regime_thresholds = get_settings().learning_atr_regime_thresholds
        for row in rows:
            enriched = dict(row)
            symbol = row.get("symbol")
            entry_ts = row.get("entry_ts")
            missing = list(row.get("_missing") or [])
            enriched["sector"] = sectors.get(symbol) if symbol else None
            if enriched["sector"] is None and symbol:
                missing.append("sector")
            # The session VWAP is not recoverable from a daily bar, so this is
            # absent on every row rather than blank on some. It is carried as a
            # column *and* marked missing, because a field the specification
            # asked for that simply does not appear reads as an oversight, while
            # one that appears absent with a reason reads as a finding.
            enriched["vwap_relationship"] = None
            missing.append("vwap_relationship")
            frame = frames.get(symbol) if symbol else None
            if frame is None:
                missing.extend(["rsi", "atr_pct", "relative_volume", "gap_pct", "trend_pct"])
                enriched.update(
                    {
                        "as_of_ts": None,
                        "bars_available": 0,
                        "close_at_entry": None,
                        "rsi": enriched.get("rsi"),
                        "atr_pct": None,
                        "relative_volume": None,
                        "gap_pct": None,
                        "trend_pct": None,
                        "above_slow_sma": None,
                        "rsi_bucket": bucket_rsi(enriched.get("rsi")),
                        "rvol_bucket": None,
                        "forward_bucket": None,
                        "market_regime": row.get("_journal_regime"),
                        "nifty_trend_pct": None,
                        "benchmark_symbol": None,
                    }
                )
                # The row must carry its own missing list as well as the builder's
                # internal ``_missing``: the internal one is dropped before the
                # row is persisted, and a consumer reading a single row needs to
                # be able to see what is absent on *that* trade.
                enriched["_atr_pct"] = None
                enriched["_missing"] = sorted(set(missing))
                enriched["missing_features"] = sorted(set(missing))
                out.append(enriched)
                continue
            context, context_missing = metrics_at(frame, entry_ts)
            missing.extend(context_missing.features)
            # A journal regime wins over the recomputed one where it exists,
            # because it was recorded at the time rather than reconstructed.
            regime = row.get("_journal_regime")
            nifty_pct, bench_prov = benchmark_provenance_at(benchmarks, entry_ts)
            benchmark_symbol = bench_prov.get("symbol")
            benchmark_kind = bench_prov.get("kind")
            benchmark_is_proxy = bench_prov.get("is_proxy")
            regime_model_version = "v1.0.0"
            if regime is None:
                regime = label_regime(nifty_pct, context.atr_pct, atr_median=None)
            trend = trend_at(frame, entry_ts)
            if trend is None:
                missing.append("trend_pct")
            # Benchmark frame for relative strength calculation
            bench_frame = benchmarks.get(benchmark_symbol) if benchmark_symbol else None
            stock_rs = relative_strength_at(frame, bench_frame, entry_ts)
            if stock_rs is None:
                missing.append("stock_relative_strength")
            # Sector relative strength: derived if sector peers are available, else missing
            sector_rs = None
            missing.append("sector_relative_strength")
            # Market breadth: point-in-time universe breadth if captured or missing
            breadth_val = None
            missing.append("market_breadth")
            # Volatility regime
            vol_regime: str | None = None
            if context.atr_pct is not None:
                if context.atr_pct >= atr_regime_thresholds["high_gte"]:
                    vol_regime = "high"
                elif context.atr_pct <= atr_regime_thresholds["low_lte"]:
                    vol_regime = "low"
                else:
                    vol_regime = "normal"
            enriched.update(
                {
                    "as_of_ts": context.as_of_ts,
                    "bars_available": context.bars_available,
                    "close_at_entry": context.close,
                    "rsi": enriched.get("rsi") if enriched.get("rsi") is not None else context.rsi,
                    "atr_pct": context.atr_pct,
                    "relative_volume": context.volume_multiple,
                    "gap_pct": context.gap_pct,
                    "trend_pct": trend,
                    "above_slow_sma": context.above_slow_sma,
                    "market_regime": regime,
                    "nifty_trend_pct": nifty_pct,
                    "benchmark_symbol": benchmark_symbol,
                    "benchmark_kind": benchmark_kind,
                    "benchmark_is_proxy": benchmark_is_proxy,
                    "regime_model_version": regime_model_version,
                    "stock_relative_strength": stock_rs,
                    "sector_relative_strength": sector_rs,
                    "market_breadth": breadth_val,
                    "volatility_regime": vol_regime,
                }
            )
            enriched["rsi_bucket"] = bucket_rsi(enriched.get("rsi"))
            enriched["rvol_bucket"] = bucket_relative_volume(enriched.get("relative_volume"))
            enriched["_atr_pct"] = context.atr_pct
            enriched["_missing"] = sorted(set(missing))
            enriched["missing_features"] = sorted(set(missing))
            out.append(enriched)
        return out

    # ------------------------------------------------------------------
    def _sector_table(self) -> dict[str, str]:
        if self._sector_cache is None:
            self._sector_cache = dict(self._artifacts.sector_table())
        return self._sector_cache

    def _load_frames(self, symbols: set[str]) -> dict[str, Any]:
        if not symbols:
            return {}
        # The cache names files ``<TICKER>-EQ.parquet``; a journal symbol may
        # arrive with or without the series suffix, so both spellings are tried.
        wanted: list[str] = []
        for symbol in sorted(symbols):
            wanted.append(f"{symbol}-EQ")
            wanted.append(symbol)
        frames = self._market_history.frames("NSEEQ", wanted)
        # Re-key bare tickers onto the canonical form so a lookup by either
        # spelling finds the same frame.
        merged: dict[str, Any] = {}
        for key, frame in frames.items():
            merged[_canonical(key)] = frame
        return merged

    def _benchmark_frames(self) -> dict[str, Any]:
        return dict(self._market_history.frames("NSEEQ", ["NIFTYBEES-EQ", "JUNIORBEES-EQ"]))

def _canonical(symbol: Any) -> str | None:
    if not symbol:
        return None
    from forward.core.symbols import canonical_symbol

    return canonical_symbol(str(symbol)) or None


def _journal_grade(raw: dict[str, Any]) -> tuple[str, str]:
    """``(grade, why)`` for one journal row.

    Three cases, and the third is the one that matters. A row explicitly marked

    forward is evidence; a row explicitly marked in-sample is a measurement; and

    a row carrying no verdict at all — which is what a journal row written

    before this column existed, or by a harness that inserts directly, looks

    like — is graded **in-sample**, with the reason recorded rather than

    silently substituted. Assuming forward there would promote every

    un-attributed fill into evidence, which is the one direction the mistake

    must never be made in.

    """
    stored = raw.get("evidence_grade")
    if is_forward_grade(stored):
        return GRADE_FORWARD, NOTE_FORWARD_STAMPED
    if stored == GRADE_IN_SAMPLE:
        return GRADE_IN_SAMPLE, NOTE_MARKED_IN_SAMPLE
    return GRADE_IN_SAMPLE, NOTE_NO_STAMP


def _dow(when: Any) -> str | None:
    if when is None:
        return None
    try:
        return ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][
            when.weekday()
        ]
    except AttributeError, IndexError:
        return None




def _ts_gap_sec(first: Any, second: Any) -> float | None:
    """Absolute seconds between two timestamps, ``None`` when uncomparable.

    Naive stamps are read as UTC, matching how the store writes them. A parse

    failure is not an error here — it just means the pair cannot be linked.

    """
    start, end = _utc_datetime(first), _utc_datetime(second)
    if start is None or end is None:
        return None
    try:
        return abs((start - end).total_seconds())
    except TypeError, OverflowError:
        return None


def _timestamp_sort_key(value: Any) -> datetime:
    """Return an aware UTC key for chronological evidence matching.

    Invalid values sort after valid timestamps. They remain unmatchable because
    ``_ts_gap_sec`` returns ``None`` for them.
    """
    return _utc_datetime(value) or datetime.max.replace(tzinfo=UTC)


def _utc_datetime(value: Any) -> datetime | None:
    """Parse a datetime value as aware UTC, or return ``None`` when invalid."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None
