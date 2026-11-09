"""The continuous paper-trading deployment runner.

What this closes
----------------

Before this module the paper engine was *request-driven*: an order filled when
somebody called ``POST /paper/deployments/{id}/orders``, prices came from the
daily parquet close, and a resting limit order was re-evaluated only if a client
happened to ask. The pieces were correct and the loop did not exist, so
``BACKTEST → PAPER → LIVE`` had no middle that actually ran.

The loop, and nothing new in the middle of it::

    live tick ──► strategy rules ──► signal ──► ExecutionService ──► OMS
                                                                     │
                                              risk gate (RiskEngine) ┘
                                                                     │
                        paper venue adapter ──► simulated fill ──┘
                                                                     │
                                     PaperLedger (a fold of order_events)
                                                                     │
                                                        positions · cash · P&L

Every step is an existing component. The runner's only job is to sequence them
on a clock, which is why it contains no matching, no costing and no P&L
arithmetic of its own.

Two rules it must not break
---------------------------

**One strategy definition.** Signals come from ``signals.rules.eval_entry`` /
``eval_exit`` — the same functions the scanner and the backtester call. A second
evaluation path here would mean paper and backtest disagreeing about what a rule
means, which is the specific failure the shared rule layer exists to prevent.

**The log is the state.** Positions and cash are folded from ``order_events`` on
every observation, never accumulated in memory. A runner that kept its own
position counters would drift from the orders that produced them, and the drift
would only be visible as money.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from forward.core.calendar_ports import MarketCalendarPort
from forward.core.historical_market_data_ports import CachedMarketFramesPort, IntradayHistoryPort
from forward.core.live_market_data_ports import MarketDataPort
from forward.core.market_tracking_ports import VwapLiquidityPort
from forward.core.order_ports import (
    OrderInstrumentLookupPort,
    OrderStoragePort,
)
from forward.core.paper_ledger_ports import PaperRunnerLedgerPort
from forward.core.paper_runner_storage_ports import PaperRunnerStoragePort
from forward.core.portfolio_ports import (
    PortfolioStoragePort,
)
from forward.core.risk_ports import (
    RiskStatePort,
)
from forward.core.runner_venue_ports import (
    RunnerPaperExecutionProfile,
    RunnerVenueProviderPort,
)
from forward.core.signal_context_ports import (
    SignalContextEnrichmentPort,
)
from forward.core.system_state_ports import SystemStateStoragePort
from forward.core.trade_journal_ports import (
    TradeJournalStoragePort,
)
from forward.paper.config import RunnerConfig
from forward.services.deployment_loop import (
    DeploymentLoop,
    RunnerDeploymentState,
    _now_ist,
    in_market_hours,
)
from forward.signals.session import MarketWeek
from forward.strategy.definition import parse_definition, resolve_rules

logger = logging.getLogger("forward.services.runner")


class RunnerTick:
    """What one pass over all deployments produced. For logging and tests."""

    at: datetime
    deployments: int
    signals: int
    orders: int
    resting_fills: int


class PaperRunner:
    """Runs saved paper and live deployments through one evaluation loop.

    One task for all deployments rather than a task each: they share a price
    source and an order log, and N tasks contending on the same SQLite control
    plane buys nothing but lock contention.

    The loop is deliberately synchronous inside. The matching is pure CPU on
    already-loaded frames; the only I/O is the control-plane write per order, and
    a fill happens rarely enough that overlapping passes would be solving a
    problem this workload does not have.
    """

    def __init__(
        self,
        *,
        ledger: PaperRunnerLedgerPort,
        interval_seconds: float,
        lookback_days: int,
        minimum_sleep_seconds: float,
        market_wait_seconds: float,
        symbol_retry_seconds: float,
        market_data: MarketDataPort | None = None,
        intraday_history: IntradayHistoryPort | None = None,
        daily_history: CachedMarketFramesPort,
        vwap_liquidity: VwapLiquidityPort,
        state_storage: SystemStateStoragePort,
        runner_storage: PaperRunnerStoragePort,
        order_storage: OrderStoragePort,
        risk_state: RiskStatePort,
        portfolio_storage: PortfolioStoragePort,
        sector_table: Mapping[str, str],
        instrument_lookup: OrderInstrumentLookupPort,
        journal_storage: TradeJournalStoragePort,
        signal_context: SignalContextEnrichmentPort,
        calendar: MarketCalendarPort,
        venue_provider: RunnerVenueProviderPort,
    ) -> None:
        """Build the deployment runner with every external dependency.

        Args:
            ledger: Paper book reader for account state.
            interval_seconds: Delay between runner passes.
            lookback_days: Required history warmup window for deployments.
            minimum_sleep_seconds: Required lower bound for clock sleeps.
            market_wait_seconds: Delay while every deployment waits for market open.
            symbol_retry_seconds: Delay before retrying feed subscriptions.
            market_data: Live feed used by deployment loops.
            intraday_history: Adapter for exact intraday contracts and history.
            daily_history: Port for cached daily market bars.
            vwap_liquidity: Port for prior-session bhavcopy liquidity.
            state_storage: Durable runner state adapter.
            runner_storage: Deployment and strategy adapter.
            order_storage: Order lifecycle adapter.
            risk_state: Risk policy and state service.
            portfolio_storage: Portfolio data adapter.
            sector_table: Canonical symbol-to-industry catalog.
            instrument_lookup: Lookup for exact listed order instruments.
            journal_storage: Trade journal adapter.
            signal_context: Service that annotates live and paper signals.
            calendar: Exchange calendar used for runner session checks.
            venue_provider: Adapter factory for paper and live execution venues.
        """
        self.ledger = ledger
        self.market_data = market_data
        self.intraday_history = intraday_history
        self.daily_history = daily_history
        self._market_week = MarketWeek(daily_history)
        self.vwap_liquidity = vwap_liquidity
        self.state_storage = state_storage
        self.runner_storage = runner_storage
        self.order_storage = order_storage
        self.risk_state = risk_state
        self.portfolio_storage = portfolio_storage
        self.sector_table = sector_table
        self.instrument_lookup = instrument_lookup
        self.journal_storage = journal_storage
        self._signal_context = signal_context
        self.calendar = calendar
        self.venue_provider = venue_provider
        self._default_interval = float(interval_seconds)
        self._lookback_days = int(lookback_days)
        self._minimum_sleep_seconds = float(minimum_sleep_seconds)
        self._market_wait_seconds = float(market_wait_seconds)
        self._symbol_retry_seconds = float(symbol_retry_seconds)
        self._last_subscription_attempt = float("-inf")

        self._venue: Any | None = None
        self._loops: dict[str, DeploymentLoop] = {}
        #: (strategy_id, version) -> resolved (entry, exit) rules or the
        #: ``_UNRESOLVED`` sentinel. A version is immutable, so once read it
        #: cannot change; caching is correct and keeps the pass off the database.
        self._rules_cache: dict[tuple[str, int], Any] = {}
        self._task: asyncio.Task | None = None
        self._clock_task: asyncio.Task | None = None
        self._clock_last_pass: str | None = None
        self._clock_duration_seconds: float | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.last_tick: RunnerTick | None = None
        self.passes = 0
        self.signals_total = 0
        self.orders_total = 0

    # ------------------------------------------------------------------
    # wiring
    # ------------------------------------------------------------------
    @property
    def venue(self) -> Any:
        if self._venue is None:
            self._venue = self.venue_provider.paper_venue()
        return self._venue

    # ------------------------------------------------------------------
    # deployment tracking
    # ------------------------------------------------------------------
    def resolve_rules(self, deployment: dict[str, Any]) -> tuple[Any, Any] | None:
        """Read one deployment version and turn it into entry/exit rules.

        This is the seam that makes "select an immutable version" mean something.
        A deployment stores ``(strategy_id, strategy_version)``; the version's
        canonical ``definition`` is the subject, and it is read by exact number
        — never "latest" — for the same reason the backtest service does it: a
        run whose subject can change after the fact is not reproducible, and a
        deployment is a run that lasts months.

        Accepted definition shapes:

        * ``{"rules": {"entry": ..., "exit": ...}}`` — the canonical rule form.
        * ``engine_key`` naming a registry strategy with a ``signal_rules()``
          classmethod — resolved through the registry.

        Returns ``None`` when none of those yield a rule pair. ``None`` is not
        "use the defaults": see ``DeploymentLoop._rules`` for why a named
        strategy must never silently run somebody else's rules.
        """
        strategy_id = deployment.get("strategy_id")
        version = deployment.get("strategy_version")
        if not strategy_id or version is None:
            return None

        key = (str(strategy_id), int(version))
        if key in self._rules_cache:
            return self._rules_cache[key]

        resolved = self._read_version_rules(key)
        # A version is immutable, so a resolved pair is safe to keep forever; an
        # unresolved one is cached too, because re-reading the store every pass
        # to reach the same refusal is pure cost.
        self._rules_cache[key] = resolved
        return resolved

    def _read_version_rules(self, key: tuple[str, int]) -> tuple[Any, Any] | None:
        """Load one version definition and coerce it to an (entry, exit) pair."""
        strategy_id, version = key
        try:
            row = self.runner_storage.strategy_version(strategy_id, version)
        except Exception:  # noqa: BLE001 — a broken store must not trade
            logger.exception(
                "runner: could not read strategy version %s#%s", strategy_id[:8], version
            )
            return None
        if row is None:
            logger.warning(
                "runner: strategy version %s#%s does not exist; not trading",
                strategy_id[:8],
                version,
            )
            return None

        raw = row.get("definition")
        definition, reason = parse_definition(raw)
        if definition is None:
            logger.warning(
                "runner: strategy version %s#%s %s; not trading",
                strategy_id[:8],
                version,
                reason,
            )
            return None

        resolution = resolve_rules(definition)
        if not resolution.ok:
            logger.warning(
                "runner: strategy version %s#%s cannot be traded — %s (keys: %s)",
                strategy_id[:8],
                version,
                resolution.reason,
                sorted(definition),
            )
            return None
        return resolution.entry, resolution.exit_rules

    def order_service(self, loop: DeploymentLoop) -> tuple[Any, Any]:
        """The pipeline bound to *this deployment's* folded portfolio.

        Built per deployment, and rebuilt on every pass, rather than sharing the
        flat default. Two consequences, both of them the point:

        * The position-aware limits (``max_position_per_symbol``,
          ``max_open_positions``, gross exposure) can actually bite. With the
          shared flat portfolio they read every position as zero, so they never
          fire — a risk limit that silently does not apply is worse than none,
          because the dashboard says it is enforced.
        * A SELL that closes a long is not misread as opening a short. The engine
          compares the sell quantity against ``portfolio.position(symbol)``
          ``.quantity``, so against a flat book every exit looks like a short and
          is rejected whenever ``allow_short`` is false — meaning stop-losses
          would never execute.

        Rebuilt each pass because the portfolio is a fold of ``order_events``:
        caching it would reintroduce exactly the drift the fold exists to prevent.
        """
        from forward.services.execution import ExecutionService
        from forward.services.orders import get_order_service
        from forward.services.portfolio_risk import portfolio_gate_for

        live_prices = loop.live_prices()
        portfolio = self.ledger.portfolio(
            loop.user_id,
            deployment_id=loop.deployment_id,
            initial_cash=float(loop.row.get("capital") or 0.0),
            prices=live_prices,
        )
        port_gate = portfolio_gate_for(
            loop.user_id,
            prices=live_prices,
            storage=self.portfolio_storage,
            sector_table=self.sector_table,
        )
        definition, _ = parse_definition(loop.row.get("definition"))
        mode = loop.row.get("mode")
        selection = self.venue_provider.for_deployment(
            mode=mode,
            engine_key=(definition or {}).get("engine_key"),
            paper_venue=self.venue,
            strategy_config=definition if definition is not None else {},
        )
        venue = selection.venue
        loop.strategy_profile = selection.profile
        if selection.portfolio is not None:
            portfolio = selection.portfolio
        loop.venue = venue
        orders = get_order_service(
            storage=self.order_storage,
            risk=self.risk_state,
            instruments=self.instrument_lookup,
            portfolio=portfolio,
            portfolio_gate=port_gate,
        )
        return ExecutionService(orders=orders, venue=venue), orders

    def running_deployments(self) -> list[dict[str, Any]]:
        """Every RUNNING paper/live deployment, across all users.

        Deliberately not scoped to one user: this is the platform's own execution
        loop, not a request. Each deployment still acts only as its own owner,
        because every service call it makes passes that owner's id.
        """
        return self.runner_storage.running_deployments()

    def watched_symbols(self) -> list[str]:
        """Every symbol a RUNNING paper deployment watches, in first-seen order (for the end-of-day data refresh)."""
        import json as _json

        out: list[str] = []
        seen: set[str] = set()
        for row in self.running_deployments():
            cfg = row.get("config")
            if isinstance(cfg, str):
                try:
                    cfg = _json.loads(cfg)
                except ValueError:
                    cfg = {}
            for sym in (cfg or {}).get("symbols") or []:
                if sym not in seen:
                    seen.add(sym)
                    out.append(sym)
        return out

    def _journal(self, *, now: datetime | None = None) -> None:
        """Bring the trade journal into agreement with each attached book.

        Failure is logged and never raised: journaling is reporting, and a
        reporting fault must not stop the execution loop that produced the trades
        it is trying to describe.
        """
        from forward.services.journal import TradeJournalService

        if not self._loops:
            return
        service = TradeJournalService(storage=self.journal_storage, ledger=self.ledger)
        for loop in list(self._loops.values()):
            try:
                service.reconcile(loop.user_id, loop.deployment_id)
            except Exception:  # noqa: BLE001 - one bad book must not stop the rest
                logger.exception("runner: journal reconcile failed for %s", loop.deployment_id[:8])

    def sync_loops(self) -> None:
        """Start a loop for each new deployment, drop the ones that stopped."""
        with self._lock:
            try:
                rows = self.running_deployments()
            except Exception:  # noqa: BLE001 - a broken store must not kill the loop
                logger.exception("runner: could not list running deployments")
                return

            keys = [(row["strategy_id"], int(row["strategy_version"])) for row in rows]
            versions = self.runner_storage.strategy_versions(keys)
            for row in rows:
                version = versions.get((row["strategy_id"], int(row["strategy_version"])))
                if version:
                    row["definition"] = version.get("definition")
            wanted = {row["deployment_id"]: row for row in rows}

            for deployment_id, row in wanted.items():
                try:
                    config = RunnerConfig.from_deployment(
                        row,
                        platform_interval_seconds=self._default_interval,
                        platform_lookback_days=self._lookback_days,
                    )
                except ValueError as exc:
                    logger.error(
                        "runner: unsupported strategy context for %s: %s", deployment_id, exc
                    )
                    self._loops.pop(deployment_id, None)
                    continue
                if deployment_id in self._loops:
                    # A config change or a restart after a crash must be picked
                    # up, so the row and config are refreshed on every sync rather than only
                    # at first sight.
                    self._loops[deployment_id].row = row
                    self._loops[deployment_id].config = config
                    self._loops[deployment_id].strategy_profile = self._strategy_profile_for(row)
                    continue
                loop = self._make_loop(row)
                if loop is None:
                    continue
                try:
                    loop.warmup()
                except Exception:  # noqa: BLE001
                    logger.exception("runner: warmup failed for %s", deployment_id[:8])
                    continue
                self._loops[deployment_id] = loop
                logger.info(
                    "runner: deployment %s is running (%d symbols)",
                    deployment_id[:8],
                    len(loop.config.symbols),
                )

            for deployment_id in list(self._loops):
                if deployment_id not in wanted:
                    logger.info("runner: deployment %s is no longer running", deployment_id[:8])
                    self._loops.pop(deployment_id, None)

        # After the loop set is settled, so the statement covers exactly the
        # deployments that are running now.
        self._ensure_feed_subscriptions()

    def _ensure_feed_subscriptions(self) -> None:
        """Put every running deployment's universe on the live tick feed.

        Requirement 2, and the hole in it. The paper engine reads its prices from
        the broadcaster — but a symbol only reaches the broadcaster's tick store
        when somebody *subscribes* to it, and the only subscriber was a browser.
        With no dashboard open, ``latest_price`` returned ``None`` for every
        symbol, and an absent tick prevented a trustworthy paper fill. The runner
        now refuses to fill until the live feed has a price for the symbol.

        Declared from the runner because the runner is the only thing that knows
        the current universe — a deployment's symbol list can change while it
        runs, and a stopped one should stop consuming feed bandwidth.

        A failed subscription leaves affected deployments without fresh ticks.
        Their evaluation path reports ``WAITING_FOR_TICKS`` and does not place
        orders until the live feed provides a usable price.
        """
        now = time.monotonic()
        if now - self._last_subscription_attempt < self._symbol_retry_seconds:
            return
        universe: dict[str, list[str]] = {}
        for loop in self._loops.values():
            profile = loop.strategy_profile
            if profile and profile.feed_subscriber:
                if self.market_data is None:
                    raise RuntimeError("Clock-sensitive deployment requires a market data adapter")
                profile.feed_subscriber(loop, self.market_data)
            for sym in loop.config.symbols:
                universe.setdefault(loop.config.exchange_of(sym), []).append(sym)
        if not universe:
            return

        if self.market_data is None:
            raise RuntimeError("Paper deployments require a market data adapter")
        self._last_subscription_attempt = now
        report = self.market_data.ensure_symbols(universe)
        if report.get("added") or report.get("removed"):
            logger.info(
                "runner: feed universe updated (+%d/-%d symbols)",
                len(report.get("added") or []),
                len(report.get("removed") or []),
            )
        unresolved = report.get("unresolved") or []
        if unresolved:
            # A symbol with no contract cannot receive live ticks, so the
            # deployment must not proceed as if its feed were healthy.
            logger.warning(
                "runner: %d symbol(s) could not be put on the feed and will not "
                "be priced from live ticks: %s",
                len(unresolved),
                ", ".join(sorted(unresolved)[:10]),
            )

    def _strategy_profile_for(self, row: dict[str, Any]) -> RunnerPaperExecutionProfile | None:
        """Resolve the configured runtime profile for a saved definition.

        Args:
            row: Saved deployment and strategy definition.

        Returns:
            The registered paper profile, or ``None`` for generic rules.
        """
        definition, _ = parse_definition(row.get("definition"))
        return self.venue_provider.profile_for((definition or {}).get("engine_key"))

    def _make_loop(self, row: dict[str, Any]) -> DeploymentLoop | None:
        config = RunnerConfig.from_deployment(
            row,
            platform_interval_seconds=self._default_interval,
            platform_lookback_days=self._lookback_days,
        )
        profile = self._strategy_profile_for(row)
        if not config.symbols:
            # A deployment with no universe would silently do nothing, which is
            # indistinguishable from a broken one. Say so once and skip.
            logger.warning(
                "runner: deployment %s has no symbols configured; skipping",
                str(row.get("deployment_id"))[:8],
            )
            return None
        return DeploymentLoop(
            row=row,
            config=config,
            wiring=self.order_service,
            ledger=self.ledger,
            venue=self.venue,
            state_storage=self.state_storage,
            calendar=self.calendar,
            rules_for=self.resolve_rules,
            market_data=self.market_data,
            intraday_history=self.intraday_history,
            daily_history=self.daily_history,
            vwap_liquidity=self.vwap_liquidity,
            strategy_profile=profile,
        )

    # ------------------------------------------------------------------
    # one pass
    # ------------------------------------------------------------------
    def pass_once(self, *, now: datetime | None = None) -> RunnerTick:
        """One evaluation pass over every running deployment.

        Public and synchronous so a test can drive the loop deterministically
        instead of sleeping on a background task.
        """
        now = now or _now_ist()
        self.sync_loops()

        signals = 0
        orders = 0
        resting_fills = 0

        # Resting orders are polled even outside market hours: an order that was
        # already accepted should be able to fill on any price that arrives, and
        # refusing to re-evaluate it after 15:30 would leave it stuck.
        unavailable: set[str] = set()
        for loop in list(self._loops.values()):
            if self._clock_owned(loop):
                continue
            try:
                loop.bind()
                resting_fills += len(loop.poll_resting_orders(now=now))
            except Exception:  # noqa: BLE001
                unavailable.add(loop.deployment_id)
                loop.state = RunnerDeploymentState.ERROR
                loop.skipped_reason = "Order synchronisation unavailable; trading paused"
                logger.exception("runner: resting poll failed for %s", loop.deployment_id[:8])

        # New entries and exits only inside the session. A signal outside it is a
        # signal about a market that is not open, and acting on one would fill at
        # a price no exchange offered.
        market_is_open = in_market_hours(calendar=self.calendar, now=now)
        if market_is_open:
            for loop in list(self._loops.values()):
                if self._clock_owned(loop):
                    continue
                if loop.deployment_id in unavailable:
                    continue
                try:
                    loop.bind()
                    acted = loop.evaluate(now=now)
                    if loop.state != RunnerDeploymentState.ERROR:
                        loop.blocked_reason = None
                except Exception as error:  # noqa: BLE001 - isolate this deployment, report its failure
                    loop.state = RunnerDeploymentState.ERROR
                    loop.blocked_reason = f"strategy evaluation failed: {error}"
                    loop.skipped_reason = loop.blocked_reason
                    loop.skipped_evaluations_count += 1
                    loop.last_pass = {
                        "at": now.isoformat(),
                        "signals": 0,
                        "orders": 0,
                        "state": loop.state,
                        "blocked": loop.blocked_reason,
                    }
                    logger.exception(
                        "runner: evaluation failed for %s", loop.deployment_id[:8]
                    )
                    continue
                # Per-deployment, not the running total. ``loop.last_pass`` is what
                # the status surface shows for *this* deployment, and a cumulative
                # figure would report the sum across every deployment as this
                # one's own — a number that grows with somebody else's activity.
                loop_orders = sum(1 for a in acted if a.get("status") not in ("REJECTED", None))
                signals += len(acted)
                orders += loop_orders
                loop.last_pass = {
                    **loop.last_pass,
                    "at": now.isoformat(),
                    "signals": len(acted),
                    "orders": loop_orders,
                    "state": loop.state,
                }
        else:
            for loop in list(self._loops.values()):
                if self._clock_owned(loop):
                    continue
                loop.state = RunnerDeploymentState.WAITING_FOR_MARKET
                loop.skipped_reason = "market closed or outside market hours"
                loop.last_pass = {
                    "at": now.isoformat(),
                    "signals": 0,
                    "orders": 0,
                    "state": loop.state,
                    "skipped_reason": loop.skipped_reason,
                }

        with self._lock:
            self.passes += 1
            self.signals_total += signals
            self.orders_total += orders

        # Journal after the trades, not during them. The journal is a projection
        # of the position fold, so it must see the fills this pass produced —
        # reconciling first would lag by one pass and reconcile before the very
        # fills it is meant to record.
        self._journal(now=now)

        tick = RunnerTick(
            at=now,
            deployments=len(self._loops),
            signals=signals,
            orders=orders,
            resting_fills=resting_fills,
        )
        self.last_tick = tick
        return tick

    # ------------------------------------------------------------------
    # the background loop
    # ------------------------------------------------------------------
    def _clock_owned(self, loop: DeploymentLoop) -> bool:
        return (
            self._clock_task is not None
            and not self._clock_task.done()
            and loop.strategy_profile is not None
            and loop.strategy_profile.clock_sensitive
        )

    def _clock_pass(self) -> None:
        # Only this worker touches clock-sensitive deployments while running.
        # Never wait for generic sync, warmup, evaluation or journal work.
        for loop in list(self._loops.values()):
            if not self._clock_owned(loop):
                continue
            try:
                loop.bind()
                loop.poll_resting_orders(now=_now_ist())
                loop.evaluate(now=_now_ist())
                if loop.state != RunnerDeploymentState.ERROR:
                    loop.blocked_reason = None
            except Exception as error:
                loop.state = RunnerDeploymentState.ERROR
                loop.blocked_reason = f"clock runner failed: {error}"
                loop.skipped_reason = loop.blocked_reason
                loop.skipped_evaluations_count += 1
                loop.last_pass = {
                    "at": _now_ist().isoformat(),
                    "signals": 0,
                    "orders": 0,
                    "state": loop.state,
                    "blocked": loop.blocked_reason,
                }
                logger.exception("runner: clock evaluation failed for %s", loop.deployment_id[:8])

    async def _run_clock(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                await asyncio.to_thread(self._clock_pass)
            except Exception:
                logger.exception("runner: clock pass failed")
            elapsed = time.monotonic() - started
            self._clock_last_pass = _now_ist().isoformat()
            self._clock_duration_seconds = elapsed
            if elapsed > 1:
                logger.warning("runner: clock pass took %.3fs", elapsed)
            await asyncio.sleep(
                max(self._default_interval - elapsed, self._minimum_sleep_seconds)
            )

    async def _run(self) -> None:
        logger.info("runner: background loop started")
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                # The pass is blocking (SQLite + pandas), so it runs in a worker
                # thread rather than on the event loop. A rule evaluation that
                # takes 200ms must not stall every HTTP request and the tick
                # stream for those 200ms.
                await asyncio.to_thread(self.pass_once)
            except Exception:  # noqa: BLE001 - the loop must survive anything
                logger.exception("runner: pass failed")

            interval = self._default_interval
            with self._lock:
                if self._loops:
                    interval = min(
                        (loop.config.interval_seconds for loop in self._loops.values()),
                        default=interval,
                    )
                if self._loops and all(
                    loop.state == RunnerDeploymentState.WAITING_FOR_MARKET
                    for loop in self._loops.values()
                ):
                    interval = self._market_wait_seconds
            elapsed = time.monotonic() - started
            await asyncio.sleep(
                max(interval - elapsed, self._minimum_sleep_seconds)
            )
        logger.info("runner: background loop stopped")

    def start(self) -> None:
        """Start the background task. Idempotent."""
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._clock_task = asyncio.create_task(self._run_clock())
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """Stop the background task and wait for the current pass to finish."""
        self._stop.set()
        tasks = [self._task, self._clock_task]
        self._task = None
        self._clock_task = None
        for task in tasks:
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(task for task in tasks if task is not None), return_exceptions=True)

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def deployment_status(self, deployment_id: str) -> dict[str, Any] | None:
        """Return the public monitoring data for one attached deployment.

        Args:
            deployment_id: The deployment to inspect.

        Returns:
            A read-only snapshot of its runner state, or ``None`` when no loop
            is attached to that deployment.
        """
        with self._lock:
            loop = self._loops.get(deployment_id)
            if loop is None:
                return None
            return {
                "state": loop.state,
                "blocked_reason": loop.blocked_reason,
                "skipped_reason": loop.skipped_reason,
                "last_tick_time": loop.last_tick_time,
                "last_tick_age_seconds": loop.last_tick_age_seconds,
                "max_tick_age_seconds": loop.venue.max_tick_age_seconds,
                "last_strategy_evaluation": loop.last_strategy_evaluation,
                "last_signal": loop.last_signal,
                "last_risk_decision": loop.last_risk_decision,
                "last_order": loop.last_order,
                "last_fill": loop.last_fill,
                "skipped_evaluations_count": loop.skipped_evaluations_count,
                "skipped_fills_count": loop.skipped_fills_count,
                "last_pass": loop.last_pass,
            }

    def status(self) -> dict[str, Any]:
        """What the loop is doing, for ``/health`` and the dashboard."""
        with self._lock:
            loops = list(self._loops.values())
            return {
                "running": self.running,
                "in_market_hours": in_market_hours(calendar=self.calendar),
                "clock_running": self._clock_task is not None and not self._clock_task.done(),
                "clock_last_pass": self._clock_last_pass,
                "clock_duration_seconds": self._clock_duration_seconds,
                "passes": self.passes,
                "deployments": [
                    {
                        "deployment_id": loop.deployment_id,
                        "strategy_id": loop.row.get("strategy_id"),
                        "symbols": len(loop.config.symbols),
                        "state": loop.state,
                        "blocked_reason": loop.blocked_reason,
                        "skipped_reason": loop.skipped_reason,
                        "last_tick_time": loop.last_tick_time.isoformat()
                        if loop.last_tick_time
                        else None,
                        "last_tick_age_seconds": loop.last_tick_age_seconds,
                        "last_strategy_evaluation": loop.last_strategy_evaluation.isoformat()
                        if loop.last_strategy_evaluation
                        else None,
                        "last_signal": loop.last_signal,
                        "last_risk_decision": loop.last_risk_decision,
                        "last_order": loop.last_order,
                        "last_fill": loop.last_fill,
                        "skipped_evaluations_count": loop.skipped_evaluations_count,
                        "skipped_fills_count": loop.skipped_fills_count,
                        "last_pass": loop.last_pass,
                    }
                    for loop in loops
                ],
                "signals_total": self.signals_total,
                "orders_total": self.orders_total,
                "last_tick": (
                    None
                    if self.last_tick is None
                    else {
                        "at": self.last_tick.at.isoformat(),
                        "signals": self.last_tick.signals,
                        "orders": self.last_tick.orders,
                        "resting_fills": self.last_tick.resting_fills,
                    }
                ),
            }
