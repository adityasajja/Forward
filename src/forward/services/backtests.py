"""Backtest service — the one place a run's lifecycle is defined.

The API route and the CLI both call in here. Nothing above this layer knows
that a backtest is executed on a thread, and nothing below it knows that a run
has a status.

Why a thread and not a task queue: a run is CPU-bound (pandas over thousands of
bars), so an asyncio task on the request loop would block every other request
for the duration. A thread releases the GIL inside pandas' C code, which is
where nearly all the time goes. A real queue (Celery/RQ) is the right answer
when runs outlive the process, and this is explicitly not that yet — see the
note in ``run_backtest`` on what happens to a run left RUNNING by a crash.

The lifecycle contract, which the tests assert against:

    submit()  → QUEUED, returns immediately with a run id
    worker    → RUNNING, then COMPLETED with artefacts, or FAILED with an error
    a failed run stores no partial artefacts (so aggregate metrics can never be
    read against a half-written trade list)
"""

from __future__ import annotations

import json
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from typing import Any

from loguru import logger

from forward.backtest.config import BacktestRunConfig, ConfigError, from_payload
from forward.backtest.engine import BacktestResult
from forward.backtest.projections import (
    compound_returns,
    curve_point,
    timestamp_text,
    trade_result,
)
from forward.backtest.runner import (
    CANCELLED,
    COMPLETED,
    FAILED,
    PROGRESS_QUEUED,
    PROGRESS_RUNNING,
    QUEUED,
    RUNNING,
    BacktestError,
    BacktestRunner,
)
from forward.config.settings import get_settings
from forward.core.backtest_ports import (
    BacktestFeedPort,
    BacktestMarketDataPort,
    BacktestStoragePort,
)
from forward.core.screener_ports import ScreenerMarketDataPort
from forward.core.signal_context_ports import (
    SignalContextEnrichmentPort,
)

#: Statuses a run can be polled in, re-exported so callers need one import.
STATUSES = (QUEUED, RUNNING, COMPLETED, FAILED, CANCELLED)


class BacktestAnalysisBusy(Exception):
    """An uncertainty analysis is already using the shared compute slot."""


class BacktestService:
    """Submit, execute and read back backtest runs."""

    def __init__(
        self,
        storage: BacktestStoragePort,
        market_data: BacktestMarketDataPort,
        signal_context: SignalContextEnrichmentPort,
        screener_market_data: ScreenerMarketDataPort,
        *,
        workers: int,
        max_persisted_trades: int,
        max_curve_points: int,
        shutdown_timeout_seconds: float,
    ) -> None:
        """Create the use case with a storage adapter.

        Args:
            storage: Port used to save runs and read their results.
            market_data: Port used to read and convert backtest market data.
            signal_context: Port used to add context to completed backtests.
            screener_market_data: Port used to resolve backtest universes.
            workers: Number of background workers for backtest execution.
        """
        self._storage = storage
        self._market_data = market_data
        self._signal_context = signal_context
        self._screener_market_data = screener_market_data
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="forward-backtest")
        self._max_persisted_trades = max_persisted_trades
        self._max_curve_points = max_curve_points
        self._shutdown_timeout_seconds = shutdown_timeout_seconds
        self._lock = threading.Lock()
        self._bootstrap_lock = threading.Lock()
        self._futures: dict[str, Any] = {}

    def monte_carlo(
        self,
        run_id: str,
        user_id: str,
        *,
        paths: int,
        block: int,
        seed: int,
    ) -> dict[str, Any]:
        """Resample one completed run's equity curve.

        Args:
            run_id: Run whose recorded equity is resampled.
            user_id: Owner of the run.
            paths: Number of bootstrap paths to calculate.
            block: Number of adjacent observations in each sampled block.
            seed: Seed for repeatable sampling.

        Returns:
            Conditional outcome ranges from the circular moving-block bootstrap.

        Raises:
            BacktestAnalysisBusy: If another uncertainty analysis is running.
            BacktestError: If the run is missing or has not completed.
            ImportError: If the optional compute group is not installed.
            ValueError: If the stored curve cannot support the requested analysis.
        """
        if not self._bootstrap_lock.acquire(blocking=False):
            raise BacktestAnalysisBusy(
                "An uncertainty analysis is already running. Try again when it finishes."
            )
        try:
            from forward.compute.bootstrap import bootstrap_equity

            curve = self.equity(run_id, user_id)
            values = [point["value"] for point in curve["equity"]]
            return bootstrap_equity(values, paths=paths, block=block, seed=seed)
        finally:
            self._bootstrap_lock.release()

    def supports_preview_strategy(self, strategy_name: str) -> bool:
        """Return whether a name exists in the built-in preview registry.

        Args:
            strategy_name: Requested built-in strategy key.

        Returns:
            Whether the preview service can build that strategy.
        """
        from forward.strategy.strategies import STRATEGIES

        return strategy_name in STRATEGIES

    def preview(
        self,
        *,
        strategy_name: str,
        feed: BacktestFeedPort,
        initial_cash: float,
        fast: int,
        slow: int,
        slippage_bps: float,
        square_off_eod: bool,
    ) -> BacktestResult:
        """Run an in-memory backtest preview without storing a run.

        Args:
            strategy_name: Key of a built-in strategy.
            feed: Historical or synthetic market observations.
            initial_cash: Starting account cash.
            fast: Fast moving-average window for SMA crossover.
            slow: Slow moving-average window for SMA crossover.
            slippage_bps: Slippage charged on each fill.
            square_off_eod: Whether open positions close at the session end.

        Returns:
            The engine result for the preview.

        Raises:
            LookupError: If the requested built-in strategy does not exist.
        """
        from forward.backtest.engine import BacktestConfig, BacktestEngine
        from forward.config.costs import slippage_model
        from forward.strategy.strategies import STRATEGIES

        strategy_class = STRATEGIES.get(strategy_name)
        if strategy_class is None:
            raise LookupError(f"unknown strategy: {strategy_name}")
        strategy = (
            strategy_class(fast=fast, slow=slow)
            if strategy_name == "sma_crossover"
            else strategy_class()
        )
        config = BacktestConfig(
            initial_cash=initial_cash,
            slippage=slippage_model(bps=slippage_bps),
            square_off_eod=square_off_eod,
        )
        return BacktestEngine(feed, strategy, config).run()

    # -------------------------------------------------------------- options
    def options(self, *, owner_user_id: str) -> dict[str, Any]:
        """Everything the configuration form needs, with availability flagged.

        Timeframes other than daily are listed as *unsupported* rather than
        omitted. A dropdown that silently lacks an option reads as a bug; one
        that offers it greyed out with "the local cache holds daily bars only"
        answers the question the user was about to ask.

        ``owner_user_id`` scopes the saved-strategy list. It is required so the
        result cannot expose another account's strategies or silently omit the
        caller's saved strategy versions.
        """
        from forward.backtest.config import (
            COST_MODELS,
            KNOWN_TIMEFRAMES,
            ROUNDING_RULES,
            SIZING_MODES,
            SOURCES,
            SUPPORTED_TIMEFRAMES,
        )

        labels = {
            "india_delivery": "Indian delivery — STT, stamp duty, GST (realistic)",
            "india_intraday": "Indian intraday (MIS) — STT on sell only, no DP charge",
            "flat_per_share": "IBKR-style flat per share (understates Indian costs)",
            "none": "No costs (A/B comparison only)",
        }
        sizing_labels = {
            "fixed_fraction": "Fixed fraction of equity per position",
            "fixed_quantity": "Fixed number of shares",
            "equal_weight": "Equal weight",
        }

        ui_config = get_settings().ui_backtest
        limits = ui_config["limits"]
        exchange_choices = ui_config["exchanges"]
        return {
            "strategies": self.strategy_registry(owner_user_id=owner_user_id),
            "timeframes": [
                {
                    "value": tf,
                    "label": tf,
                    "available": tf in SUPPORTED_TIMEFRAMES,
                    "reason": None
                    if tf in SUPPORTED_TIMEFRAMES
                    else "the local cache holds daily bars only",
                }
                for tf in KNOWN_TIMEFRAMES
            ],
            "cost_models": [{"value": m, "label": labels.get(m, m)} for m in COST_MODELS],
            "sizing_modes": [{"value": m, "label": sizing_labels.get(m, m)} for m in SIZING_MODES],
            "rounding_rules": [{"value": rule, "label": rule.title()} for rule in ROUNDING_RULES],
            "sources": [{"value": s, "label": s} for s in SOURCES],
            "exchanges": exchange_choices,
            "universes": self._universes(),
            "limits": {
                "max_symbols": limits["max_symbols"],
                "min_capital": limits["min_capital"],
                "max_persisted_trades": limits["max_persisted_trades"],
            },
        }

    def _universes(self) -> list[dict[str, Any]]:
        exchange = get_settings().ui_backtest["exchange"]
        return self._screener_market_data.universes(exchange)

    def strategy_registry(self, *, owner_user_id: str) -> list[dict[str, Any]]:
        """Strategies that can be backtested, with their version counts.

        Merges the built-in registry with the user's saved strategies, because a
        backtest can name either and a form that only showed one would make the
        other unreachable from the UI.

        ``owner_user_id`` is required so every saved-strategy read is scoped to
        the account that requested it.
        """
        from forward.strategy.strategies import STRATEGIES

        rows: list[dict[str, Any]] = []
        for name in sorted(STRATEGIES):
            rows.append(
                {
                    "kind": "builtin",
                    "key": name,
                    "name": name,
                    "strategy_id": None,
                    "versions": [],
                }
            )

        saved = self._storage.saved_strategies(owner_user_id)
        for row in saved:
            versions = self._storage.strategy_versions(row["strategy_id"], owner_user_id)
            rows.append(
                {
                    "kind": "saved",
                    "key": row.get("engine_key"),
                    "name": row.get("name"),
                    "strategy_id": row["strategy_id"],
                    "description": row.get("description"),
                    "versions": [
                        {
                            "version": v["version"],
                            "created_at": timestamp_text(v.get("created_at")),
                            "change_note": v.get("change_note"),
                        }
                        for v in versions
                    ],
                }
            )
        return rows

    # ---------------------------------------------------------------- submit
    def submit(self, payload: dict, *, user_id: str) -> dict[str, Any]:
        """Validate, persist a QUEUED run, and start it. Returns immediately."""
        try:
            config = from_payload(payload)
        except ConfigError as exc:
            raise BacktestError(str(exc), code=exc.code, status=422) from exc

        # An equal-weight run over one symbol with a 10% target is a
        # contradiction the user cannot see: they asked to spread risk over a
        # set that has one member. Say so rather than silently dividing by one.
        self._version_definition(config, user_id)
        if config.sizing.mode == "equal_weight" and len(self._resolve_for_check(config)) == 1:
            raise BacktestError(
                "equal-weight sizing needs more than one symbol to spread across",
                code="equal_weight_needs_many",
                status=422,
            )

        row = self._storage.create_run(
            user_id=user_id,
            config=config.to_dict(),
            engine_key=config.engine_key or config.strategy,
            strategy_id=config.strategy_id,
            strategy_version=config.strategy_version,
        )

        run_id = row["run_id"]
        future = self._pool.submit(self._execute, run_id, user_id, config)
        with self._lock:
            self._futures[run_id] = future
        logger.info("backtest {} submitted ({} symbols)", run_id, len(config.symbols))
        return {
            "run_id": run_id,
            "status": QUEUED,
            "progress": PROGRESS_QUEUED,
            "config": config.to_dict(),
            "fingerprint": config.fingerprint(),
        }

    def readiness(self, payload: dict[str, Any], *, user_id: str) -> dict[str, Any]:
        """Check a backtest setup without saving or starting a run.

        Args:
            payload: Backtest settings from the authoring form.
            user_id: Account requesting the readiness check.

        Returns:
            Per-symbol history and data-quality checks.
        """
        try:
            config = from_payload(payload)
        except ConfigError as exc:
            raise BacktestError(str(exc), code=exc.code, status=422) from exc

        from forward.backtest.readiness import check_readiness

        return check_readiness(self._build_runner(config, user_id))

    def _resolve_for_check(self, config: BacktestRunConfig) -> list[str]:
        """Symbol count without touching the cache."""
        if config.symbols:
            return [s for s in config.symbols if str(s).strip()]
        if config.universe:
            return self._universe_resolver(config.universe, config.exchange)
        return []

    # --------------------------------------------------------------- execute
    def _version_definition(
        self, config: BacktestRunConfig, user_id: str
    ) -> dict | None:
        """Read a saved strategy version's definition, or None for a built-in.

        A version is loaded by its exact number, never "latest": a run whose
        subject can change after the fact is not reproducible, which is the
        whole point of persisting the config.

        Args:
            config: Validated settings that identify the saved version.
            user_id: Account that must own the saved strategy.

        Returns:
            The stored strategy definition, or ``None`` for a built-in.

        Raises:
            BacktestError: If the exact version is missing or not owned.
        """
        if not (config.strategy_id and config.strategy_version is not None):
            return None
        row = self._storage.strategy_version(
            config.strategy_id, int(config.strategy_version), user_id
        )
        if row is None:
            raise BacktestError(
                f"strategy version {config.strategy_id}#{config.strategy_version} does not exist",
                code="version_not_found",
                status=404,
            )
        raw = row.get("definition") or "{}"
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError as exc:
            raise BacktestError(
                f"strategy version {config.strategy_version} has an unreadable definition",
                code="bad_version_definition",
                status=422,
            ) from exc
        return parsed if isinstance(parsed, dict) else {}

    def _universe_resolver(self, universe: str, exchange: str) -> list[str]:
        """Expand a named universe. Injected into the runner.

        The runner is a compute layer and cannot import the screener, so the
        resolution is handed to it as a callable rather than reached for.
        """
        return self._screener_market_data.symbols_for(universe, exchange)

    def _build_runner(self, config: BacktestRunConfig, user_id: str) -> BacktestRunner:
        """Build a runner with the services it needs for this exact config.

        Args:
            config: Validated backtest settings.
            user_id: Account that owns the selected strategy version.

        Returns:
            A runner wired to market data and strategy-version storage.
        """
        return BacktestRunner(
            config,
            version_definition=self._version_definition(config, user_id),
            universe_resolver=self._universe_resolver,
            market_data=self._market_data,
            max_persisted_trades=self._max_persisted_trades,
            max_curve_points=self._max_curve_points,
        )

    def hold_benchmark(self, run_id: str, user_id: str) -> dict[str, Any]:
        """Compare a completed run with holding its requested instruments.

        Args:
            run_id: Completed backtest run to inspect.
            user_id: Owner requesting the comparison.

        Returns:
            Benchmark results for the run's loaded market window.
        """
        from forward.backtest.config import from_payload
        from forward.backtest.hold_benchmark import hold_benchmark

        self.metrics(run_id, user_id)
        run = self.run(run_id, user_id)
        config = from_payload(run["config"])
        runner = self._build_runner(config, user_id)
        _, frames, _ = runner.build_feed()
        dates = [timestamp for frame in frames.values() for timestamp in frame.index]
        result = hold_benchmark(
            frames,
            config.start or str(min(dates))[:10],
            config.end or str(max(dates))[:10],
        )
        result["requested_symbols"] = len(runner.resolve_symbols())
        return result

    def _execute(self, run_id: str, user_id: str, config: BacktestRunConfig) -> None:
        """Worker body. Never raises: every failure is recorded on the run."""
        try:
            self._storage.start_run(run_id, user_id, PROGRESS_RUNNING)
        except Exception as exc:  # noqa: BLE001
            # A run whose status row cannot be updated must not be silently
            # abandoned: record why, so the caller sees FAILED rather than a run
            # parked at QUEUED forever.
            logger.error("could not mark backtest {} running: {}", run_id, exc)
            self._fail(run_id, user_id, f"run could not be started: {exc}")
            return

        try:
            outcome = self._build_runner(config, user_id).execute()
        except BacktestError as exc:
            self._fail(run_id, user_id, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 — a worker must record, not crash
            detail = f"{type(exc).__name__}: {exc}"
            logger.error("backtest {} failed: {}\n{}", run_id, detail, traceback.format_exc())
            self._fail(run_id, user_id, detail)
            return

        try:
            metrics = dict(outcome.metrics)
            metrics["warnings"] = outcome.warnings
            metrics["signal_context_model_version"] = _context_model_version()
            written = self._storage.complete_run(
                run_id,
                user_id,
                metrics=metrics,
                data_fingerprint=outcome.data_fingerprint,
                equity=outcome.equity,
                drawdown=outcome.drawdown,
                exposure=outcome.exposure,
                trades=outcome.trades,
                monthly=outcome.monthly,
            )
            if not written:
                # The storage transaction leaves artifacts untouched when the
                # run is already terminal, so a late worker cannot erase a
                # completed result.
                logger.info("backtest {} finished but was no longer running", run_id)
                return
            logger.info("backtest {} completed: {} trades", run_id, metrics.get("num_trades"))
        except Exception as exc:  # noqa: BLE001
            logger.error("could not persist backtest {}: {}", run_id, exc)
            self._fail(run_id, user_id, f"results could not be stored: {exc}")
            return

        self._enrich_signal_context(run_id, user_id)

    def _enrich_signal_context(self, run_id: str, user_id: str) -> None:
        """Best-effort, post-completion context annotation for a run's trades.

        Deliberately outside the persistence try/except: enrichment is an
        annotation on an already-COMPLETED run and must never turn it FAILED.
        """
        try:
            self._signal_context.enrich_run(run_id, user_id)
        except Exception as exc:  # noqa: BLE001 — annotation is not a gate
            logger.warning("signal context enrichment for backtest %s failed: %s", run_id, exc)

    def _fail(self, run_id: str, user_id: str, error: str) -> None:
        try:
            # No partial artefacts under a FAILED run: a metrics reader must
            # never find a trade list that stopped mid-write.
            self._storage.fail_run(run_id, user_id, error)
        except Exception as exc:  # noqa: BLE001
            logger.error("could not record failure for backtest {}: {}", run_id, exc)

    # ------------------------------------------------------------------ read
    def run(self, run_id: str, user_id: str) -> dict[str, Any]:
        row, trades = self._storage.run(run_id, user_id)
        if row is None:
            raise BacktestError("backtest run not found", code="not_found", status=404)

        return {
            "run_id": row["run_id"],
            "status": row["status"],
            "progress": float(row.get("progress") or 0.0),
            "engine_key": row.get("engine_key"),
            "strategy_id": row.get("strategy_id"),
            "strategy_version": row.get("strategy_version"),
            "created_at": timestamp_text(row.get("created_at")),
            "started_at": timestamp_text(row.get("started_at")),
            "finished_at": timestamp_text(row.get("finished_at")),
            "data_fingerprint": row.get("data_fingerprint"),
            "error": row.get("error"),
            "config": _loads(row.get("config")),
            "metrics": _loads(row.get("metrics")),
            "num_trades": trades,
            "reproducible": bool(row.get("data_fingerprint")),
        }

    def history(
        self, user_id: str, *, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        if status and status.upper() not in STATUSES:
            raise BacktestError(
                f"unknown status {status!r}; known: {', '.join(STATUSES)}",
                code="unknown_status",
                status=422,
            )

        rows, total = self._storage.history(
            user_id,
            status=status.upper() if status else None,
            limit=limit,
            offset=offset,
        )

        runs = []
        for row in rows:
            metrics = _loads(row.get("metrics")) or {}
            config = _loads(row.get("config")) or {}
            runs.append(
                {
                    "run_id": row["run_id"],
                    "status": row["status"],
                    "progress": float(row.get("progress") or 0.0),
                    "strategy": row.get("engine_key") or config.get("strategy"),
                    "strategy_id": row.get("strategy_id"),
                    "strategy_version": row.get("strategy_version"),
                    "created_at": timestamp_text(row.get("created_at")),
                    "finished_at": timestamp_text(row.get("finished_at")),
                    "symbols": len(config.get("symbols") or []) or config.get("universe"),
                    "total_return_pct": metrics.get("total_return_pct"),
                    "sharpe": metrics.get("sharpe"),
                    "max_drawdown_pct": metrics.get("max_drawdown_pct"),
                    "num_trades": metrics.get("num_trades"),
                    "error": row.get("error"),
                }
            )
        return {"runs": runs, "total": total}

    def metrics(self, run_id: str, user_id: str) -> dict[str, Any]:
        row = self._completed(run_id, user_id)
        payload = _loads(row.get("metrics")) or {}
        return {
            "run_id": run_id,
            "metrics": payload,
            "data_fingerprint": row.get("data_fingerprint"),
        }

    def equity(self, run_id: str, user_id: str) -> dict[str, Any]:
        """Equity plus the drawdown and exposure curves, each as {ts, value}."""
        self._completed(run_id, user_id)
        curves = self._storage.curves(run_id)

        return {
            "run_id": run_id,
            "equity": [curve_point(row) for row in curves["equity"]],
            "drawdown": [curve_point(row) for row in curves["drawdown"]],
            "exposure": [curve_point(row) for row in curves["exposure"]],
        }

    def trades(
        self, run_id: str, user_id: str, *, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        self._completed(run_id, user_id)
        total, rows = self._storage.trades(run_id, limit=limit, offset=offset)

        return {
            "run_id": run_id,
            "trades": [trade_result(r) for r in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def _effective_params(self, row: dict[str, Any], user_id: str) -> dict[str, Any]:
        """The parameters that actually ran, for the trade-detail read.

        Reading ``config["params"]`` alone is wrong for a run pinned to a saved
        version: those parameters live in the version's stored definition and
        never appear in the run's own config, so the trade detail reported ``{}``
        — a run that says nothing about the parameters it used, which is exactly
        the question requirement 4 asks the trade view to answer.

        The precedence mirrors ``BacktestRunner.build_strategy``: the stored
        definition is the baseline, and explicit run params win on top of it, so
        a user can vary one value without forking a version.
        """
        config = _loads(row.get("config")) or {}
        params = dict(config.get("params") or {})
        strategy_id = row.get("strategy_id")
        version = row.get("strategy_version")
        if not (strategy_id and version is not None):
            return params
        definition = self._version_definition(
            BacktestRunConfig(
                strategy_id=strategy_id,
                strategy_version=int(version),
                params=params,
            ),
            user_id,
        )
        stored = (definition or {}).get("params")
        return {**stored, **params} if isinstance(stored, dict) else params

    def trade(self, run_id: str, user_id: str, seq: int) -> dict[str, Any]:
        """One trade in full, including the signal conditions behind its entry."""
        row = self._completed(run_id, user_id)
        trade = self._storage.trade(run_id, seq)
        if trade is None:
            raise BacktestError("trade not found in this run", code="not_found", status=404)

        config = _loads(row.get("config")) or {}
        out = trade_result(trade)
        out["strategy"] = {
            "engine_key": row.get("engine_key"),
            "strategy_id": row.get("strategy_id"),
            "version": row.get("strategy_version"),
            # The effective parameters, not just the run's overrides: a version
            # backtest must be able to say what the version contained.
            "params": self._effective_params(row, user_id),
        }
        out["exits"] = {
            "stop_loss_pct": (config.get("stops") or {}).get("stop_loss_pct"),
            "take_profit_pct": (config.get("stops") or {}).get("take_profit_pct"),
            "trailing_stop_pct": (config.get("stops") or {}).get("trailing_stop_pct"),
        }
        out["costs"] = config.get("costs") or {}
        out["sizing"] = config.get("sizing") or {}
        return out

    def monthly(self, run_id: str, user_id: str) -> dict[str, Any]:
        self._completed(run_id, user_id)
        rows = self._storage.monthly(run_id)

        years: dict[int, dict[int, float | None]] = {}
        for row in rows:
            years.setdefault(int(row["year"]), {})[int(row["month"])] = row.get("return_pct")

        return {
            "run_id": run_id,
            "matrix": [
                {
                    "year": year,
                    "months": [months.get(m) for m in range(1, 13)],
                    "year_total": compound_returns(months.values()),
                }
                for year, months in sorted(years.items())
            ],
        }

    def cancel(self, run_id: str, user_id: str) -> dict[str, Any]:
        changed = self._storage.cancel_run(run_id, user_id)
        if not changed:
            raise BacktestError(
                "run is not cancellable (already finished, or not yours)",
                code="not_cancellable",
                status=409,
            )
        return {"run_id": run_id, "status": CANCELLED}

    # ------------------------------------------------------------------
    def _completed(self, run_id: str, user_id: str) -> dict[str, Any]:
        """Read a run that must be COMPLETED, with a specific error if not.

        A results reader asking for metrics on a RUNNING run gets told it is
        still running, rather than an empty metric set that looks like a
        completed run which made nothing.
        """
        row = self._storage.get_run(run_id, user_id)
        if row is None:
            raise BacktestError("backtest run not found", code="not_found", status=404)
        if row["status"] != COMPLETED:
            raise BacktestError(
                f"run is {row['status'].lower()}, not completed",
                code="not_completed",
                status=409,
            )
        return row

    # ------------------------------------------------------------------
    def shutdown(self) -> None:
        """Stop accepting work and let in-flight runs finish.

        A worker that outlives its service
        writes into a database that may already have been torn down — which in
        tests surfaced as an intermittent error on an unrelated test, and in
        production would mean a run completing against a database that had been
        rotated. Waiting a bounded time costs a shutdown a few seconds; not
        waiting costs correctness.

        ``cancel_futures`` drops work that has not started, since there is no
        point running a queued backtest against a service being shut down. Work
        already executing is allowed to finish rather than being interrupted
        mid-write, which would leave a half-written artefact set.

        After the timeout the threads are abandoned — the old behaviour — so a
        genuinely hung run cannot block process exit forever.
        """
        self._pool.shutdown(wait=False, cancel_futures=True)
        deadline = time.monotonic() + self._shutdown_timeout_seconds
        with self._lock:
            pending = list(self._futures.values())
        for future in pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.warning(
                    "backtest shutdown timed out with {} run(s) still executing",
                    sum(1 for f in pending if not f.done()),
                )
                return
            with suppress(Exception):
                future.result(timeout=remaining)
        with self._lock:
            self._futures.clear()


def _loads(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    import json

    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _context_model_version() -> str | None:
    """The signal-context scoring model this run's trades were tagged with.

    Stamped on the run's metrics so a reader knows which model produced the
    contexts; ``None`` if the engine cannot be imported (it is an annotation).
    """
    try:
        from forward.signal_context import DEFAULT_CONTEXT_MODEL_V1

        return DEFAULT_CONTEXT_MODEL_V1.version
    except Exception:  # noqa: BLE001
        return None


__all__ = ["BacktestService", "STATUSES"]
