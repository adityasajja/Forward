"""Run one saved paper or live deployment.

The loop owns one deployment's market history, rule evaluation, orders, and
status. ``PaperRunner`` owns the shared clock and coordinates many loops.
Position and cash values come from the order ledger, not private counters.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from forward.core.calendar_ports import MarketCalendarPort
from forward.core.enums import OrderType
from forward.core.historical_market_data_ports import CachedMarketFramesPort, IntradayHistoryPort
from forward.core.live_market_data_ports import MarketDataPort
from forward.core.market_tracking_ports import VwapLiquidityPort
from forward.core.paper_ledger_ports import PaperRunnerLedgerPort
from forward.core.paper_models import TickPrice
from forward.core.runner_venue_ports import (
    RunnerPaperExecutionProfile,
)
from forward.core.session import (
    IST,
    RUNNER_INTRADAY_TIMEFRAME_MINUTES,
)
from forward.core.system_state_ports import SystemStateStoragePort
from forward.core.time import utcnow
from forward.execution.oms import OrderDraft
from forward.paper.config import RunnerConfig
from forward.services.deployment_entry_ranking import prioritize_entry_candidates
from forward.services.deployment_frames import completed_intraday_bars
from forward.signals.session import build_context

logger = logging.getLogger("forward.services.runner")


class RunnerDeploymentState:
    WAITING_FOR_MARKET = "WAITING_FOR_MARKET"
    WAITING_FOR_TICKS = "WAITING_FOR_TICKS"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    ERROR = "ERROR"


def _now_ist() -> datetime:
    """The clock the market calendar is asked about.

    Naive datetimes mean Indian time to the calendar. The runner used to default to
    naive UTC, which put the "open" window at 14:45 to 21:00 Indian time and left a run
    waiting for a market that was already trading.
    """
    return datetime.now(IST)


def in_market_hours(*, calendar: MarketCalendarPort, now: datetime | None = None) -> bool:
    """Return whether the injected exchange calendar is open.

    Args:
        calendar: Exchange calendar used by this runner.
        now: Optional local market time to check.

    Returns:
        Whether the market is open at ``now``.
    """
    return calendar.is_market_open(now)


@dataclass
class DeploymentLoop:
    """One running deployment. Owns its own history cache, not its own positions.

    The history cache is per-deployment because two deployments of the same
    strategy over different universes should not share a warmup frame; the
    positions are *not* held here because the order log already holds them.
    """

    row: dict[str, Any]
    config: RunnerConfig
    #: Called with this deployment row, returns an (execution, orders) pair
    #: bound to the deployment's own folded portfolio. A callable rather than
    #: two objects because the portfolio is rebuilt from the log on every
    #: pass — see ``PaperRunner.order_service``.
    wiring: Callable[[DeploymentLoop], tuple[Any, Any]]
    ledger: PaperRunnerLedgerPort
    venue: Any
    state_storage: SystemStateStoragePort
    calendar: MarketCalendarPort
    daily_history: CachedMarketFramesPort
    vwap_liquidity: VwapLiquidityPort
    #: Resolves this deployment's (entry, exit) rules, or ``None`` when they
    #: cannot be resolved. Injected rather than imported so the runner does not
    #: reach into the strategy-version store itself.
    rules_for: Callable[[dict[str, Any]], tuple[Any, Any] | None]
    strategy_profile: RunnerPaperExecutionProfile | None = None
    market_data: MarketDataPort | None = None
    intraday_history: IntradayHistoryPort | None = None
    #: symbol -> daily frame, loaded once and appended to as bars close.
    frames: dict[str, pd.DataFrame] = field(default_factory=dict)
    #: The forming bar's date, so a day boundary is detected without a calendar.
    frame_day: dict[str, Any] = field(default_factory=dict)
    #: Intraday mode only: the currently-forming bar per symbol, accumulated
    #: tick by tick — {"start": bar's own open-time, "open"/"high"/"low"/
    #: "close": float, "volume": float}. See `_refresh_intraday_bar`.
    intraday_bar: dict[str, dict[str, Any]] = field(default_factory=dict)
    intraday_loaders: dict[str, Callable[[], pd.DataFrame]] = field(default_factory=dict)
    intraday_refresh: dict[str, datetime] = field(default_factory=dict)
    intraday_attempt: dict[str, datetime] = field(default_factory=dict)

    #: Signals already acted on, keyed by (symbol, rule, bar) so a rule that
    #: stays true for an hour does not open an hour of orders.
    acted: set[tuple[str, str, str]] = field(default_factory=set)
    #: The last completed bar a position was opened on, per symbol. Bar-based exits read only completed
    #: bars, so they cannot say anything new about a position until a newer bar exists: a mean-reversion
    #: entry ("three down days") sits below its short average by construction, and without this guard the
    #: trend-break exit sold it seconds after the buy.
    entered_bar: dict[str, str] = field(default_factory=dict)
    #: The first fresh price seen per symbol each day: (day, price, minutes after the open).
    _first_price: dict[str, tuple[Any, float, float]] = field(default_factory=dict)
    #: The account as folded during the current pass; see ``_portfolio``.
    _in_pass: bool = False
    _fold: Any = None
    #: Set when rules emitted but nothing was traded, for the status surface.
    last_pass: dict[str, Any] = field(default_factory=dict)
    started_at: datetime = field(default_factory=utcnow)
    #: Why this deployment is not trading, when it is not. ``None`` means it
    #: resolved its rules and is evaluating normally.
    blocked_reason: str | None = None
    #: (execution, orders) for the current pass, set by ``bind``.
    _bound: tuple[Any, Any] | None = None

    # Observability state and metrics
    state: str = field(default=RunnerDeploymentState.RUNNING)
    last_tick_time: datetime | None = None
    last_tick_age_seconds: float | None = None
    last_strategy_evaluation: datetime | None = None
    last_signal: dict[str, Any] | None = None
    last_risk_decision: dict[str, Any] | None = None
    last_order: dict[str, Any] | None = None
    last_fill: dict[str, Any] | None = None
    last_sizing: dict[str, Any] | None = None
    skipped_evaluations_count: int = 0
    skipped_fills_count: int = 0
    skipped_reason: str | None = None

    @property
    def timeframe_minutes(self) -> int | None:
        """Minutes per bar in intraday mode, or `None` for the original daily
        mode. Every other intraday method checks this first — it is the one
        switch between "one bar per calendar day" and "one bar per N minutes"."""
        return RUNNER_INTRADAY_TIMEFRAME_MINUTES.get(self.config.timeframe)

    @property
    def user_id(self) -> str:
        return self.row["user_id"]

    @property
    def deployment_id(self) -> str:
        return self.row["deployment_id"]

    def bind(self) -> None:
        """Resolve the pipeline once, at the start of a pass.

        Both ``execution`` and ``orders`` are read many times inside one pass and
        folding the log twice would price the risk gate against a different book
        than the venue matched against. Binding per pass is the granularity that
        is both correct and cheap — the fold is a handful of indexed reads, not
        something to hold across passes, because a fill recorded this pass must
        be visible to the next one.
        """
        execution, orders = self.wiring(self)
        self._bound = (execution, orders)

    @property
    def execution(self) -> Any:
        return self._bound[0]

    @property
    def orders(self) -> Any:
        return self._bound[1]

    # ------------------------------------------------------------------
    # history
    # ------------------------------------------------------------------
    def warmup(self) -> None:
        """Load each symbol's history once, so rules have indicator context.

        Daily mode reads the maintained local cache only (no broker call needed
        or made — see `load_daily`). Intraday mode has no such cache to read
        (see `IntradayHistoryPort`), so this makes one broker call per symbol to seed
        today's bars so far; a deployment started well after the open will
        simply have missed the earlier bars for *this* session (an opening-range
        rule, for instance, would have nothing to measure until tomorrow) —
        there is no session history to recover after the fact.
        """
        if self.strategy_profile and self.strategy_profile.skip_history_warmup:
            return  # Custom engines use fresh feed metadata; no history downloads.
        if self.timeframe_minutes is not None:
            self._warmup_intraday()
            return

        loaded = 0
        for symbol in self.config.symbols:
            frame = self.daily_history.daily_frame(symbol, self.config.exchange_of(symbol))
            if frame.empty:
                logger.debug("runner %s: no history for %s", self.deployment_id[:8], symbol)
                continue
            self.frames[symbol] = frame
            if "ts" in frame.columns and len(frame):
                self.frame_day[symbol] = pd.Timestamp(frame["ts"].iloc[-1]).date()
            loaded += 1
        logger.info(
            "runner %s warmed %d/%d symbols",
            self.deployment_id[:8],
            loaded,
            len(self.config.symbols),
        )

    def _warmup_intraday(self) -> None:
        """Load recent bars through the configured intraday history port."""
        if self.intraday_history is None:
            raise RuntimeError("Intraday history adapter is not configured")
        resolved = self._rules()
        history_bars = (
            max(
                (getattr(rule, "min_history_bars", 200) for rule in resolved),
                default=200,
            )
            if resolved
            else 200
        )
        lookback_days = min(
            self.config.lookback_days,
            max(
                5,
                int((history_bars + 100) * self.timeframe_minutes / 375 * 2) + 5,
            ),
        )
        loaders = self.intraday_history.loaders_for(
            {symbol: self.config.exchange_of(symbol) for symbol in self.config.symbols},
            interval=self.config.timeframe,
            lookback_days=lookback_days,
        )
        loaded = 0
        for symbol in self.config.symbols:
            loader = loaders[symbol]
            self.intraday_loaders[symbol] = loader
            frame = loader()
            self.frames[symbol] = frame
            if not frame.empty:
                loaded += 1
        logger.info(
            "runner %s warmed %d/%d symbols intraday (%s)",
            self.deployment_id[:8],
            loaded,
            len(self.config.symbols),
            self.config.timeframe,
        )

    def _refresh_if_new_day(self, symbol: str, price: float) -> pd.DataFrame:
        """Start a new bar when the session rolls over; otherwise update in place.

        Without this the forming bar would absorb every historical session into
        one enormous candle and every SMA would be wrong. The day boundary is
        detected from the bar's own timestamp rather than from a calendar, so a
        weekend or a holiday needs no special case.
        """
        frame = self.frames[symbol]
        today = datetime.now(IST).date()
        previous = self.frame_day.get(symbol)
        if previous is not None and previous == today:
            return frame

        # A new session: persist the previous forming bar by reloading, then let
        # the caller append today's.
        reloaded = self.daily_history.daily_frame(symbol, self.config.exchange_of(symbol))
        if not reloaded.empty:
            frame = reloaded
            self.frames[symbol] = frame
        self.frame_day[symbol] = today
        return frame

    def _closed_frame(
        self, symbol: str, now: datetime, cal: Any
    ) -> tuple[pd.DataFrame | None, int]:
        """Completed daily bars only, and how many sessions the newest one is behind.

        Today's bar is dropped (it is not finished). A history more than zero sessions behind the
        last completed session means the end-of-day refresh failed, so signals read from it would
        describe an older market than the one being traded.
        """
        from forward.services.data_status import expected_session, sessions_behind

        frame = self.frames.get(symbol)
        if frame is None or frame.empty or "ts" not in frame.columns:
            return None, 0
        days = pd.to_datetime(frame["ts"]).dt.date
        closed = frame.loc[days < now.astimezone(IST).date() if now.tzinfo else days < now.date()]
        if closed.empty:
            return None, 0
        last = pd.Timestamp(closed["ts"].iloc[-1]).date()
        behind = sessions_behind(last, expected_session(now, cal), cal)
        return closed.reset_index(drop=True), behind

    def live_frame(self, symbol: str) -> pd.DataFrame | None:
        """The symbol's history with the current price appended as a forming bar."""
        price = self._price(symbol)
        if price is None:
            return None

        if self.timeframe_minutes is not None:
            return self._refresh_intraday_bar(symbol, price)

        frame = self.frames.get(symbol)
        if frame is None or frame.empty:
            return None
        frame = self._refresh_if_new_day(symbol, price)
        from forward.signals.rules import append_live_bar

        return append_live_bar(frame, price)

    def _refresh_intraday_bar(self, symbol: str, price: float) -> pd.DataFrame | None:
        """Accumulate real OHLC for the current N-minute bar from ticks, and
        finalize it into `self.frames[symbol]` when the bar boundary rolls over.

        Unlike the daily path's `append_live_bar` (which re-approximates "today"
        as a single flat point on every call, fine when one day barely moves a
        200-day SMA), an intraday bar's own high/low often *is* the signal — an
        opening-range or VWAP rule needs the bar's real range, not a snapshot of
        wherever the price happened to be on the one tick that got sampled.
        """
        n = self.timeframe_minutes
        if n is None:
            return None
        now = getattr(self, "_evaluation_time", None) or datetime.now(IST)
        now = now.astimezone(IST) if now.tzinfo else now.replace(tzinfo=IST)
        bar_start = now.replace(second=0, microsecond=0)
        bar_start = bar_start - timedelta(minutes=bar_start.minute % n)

        bar = self.intraday_bar.get(symbol)
        if bar is not None and bar["start"] != bar_start:
            # The bar just rolled over: finalize the one that just closed.
            history = self.frames.get(symbol)
            row = pd.DataFrame(
                [
                    {
                        "ts": bar["start"],
                        "open": bar["open"],
                        "high": bar["high"],
                        "low": bar["low"],
                        "close": bar["close"],
                        "volume": bar["volume"],
                    }
                ]
            )
            if symbol not in self.intraday_loaders:
                combined = (
                    row
                    if history is None or history.empty
                    else pd.concat([history, row], ignore_index=True)
                )
                self.frames[symbol] = combined.drop_duplicates("ts", keep="last").reset_index(
                    drop=True
                )
            bar = None

        if bar is None:
            bar = {
                "start": bar_start,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 0.0,
            }
            self.intraday_bar[symbol] = bar
        else:
            bar["high"] = max(bar["high"], price)
            bar["low"] = min(bar["low"], price)
            bar["close"] = price

        history = self.frames.get(symbol)
        if history is not None and not history.empty:
            stamps = pd.to_datetime(history["ts"])
            boundary = pd.Timestamp(bar_start)
            if stamps.dt.tz is None:
                boundary = boundary.tz_localize(None)
            else:
                boundary = boundary.tz_convert(stamps.dt.tz)
            history = history.loc[stamps < boundary].reset_index(drop=True)
        forming = pd.DataFrame(
            [
                {
                    "ts": bar["start"],
                    "open": bar["open"],
                    "high": bar["high"],
                    "low": bar["low"],
                    "close": bar["close"],
                    "volume": bar["volume"],
                }
            ]
        )
        return (
            forming
            if history is None or history.empty
            else pd.concat([history, forming], ignore_index=True)
        )

    def _completed_intraday_frame(self, symbol: str, frame: pd.DataFrame, now: datetime):
        """Read completed exchange candles, never the tick-sampled forming candle."""
        n = self.timeframe_minutes
        local = now.astimezone(IST) if now.tzinfo else now.replace(tzinfo=IST)
        boundary = local.replace(second=0, microsecond=0) - timedelta(minutes=local.minute % n)
        loader = self.intraday_loaders.get(symbol)
        if loader is not None:
            last_attempt = self.intraday_attempt.get(symbol)
            if self.intraday_refresh.get(symbol) != boundary and (
                last_attempt is None or (local - last_attempt).total_seconds() >= 5
            ):
                self.intraday_attempt[symbol] = local
                self.frames[symbol] = loader()

            history = self.frames.get(symbol)
        else:
            history = frame.iloc[:-1]  # Deterministic replay / explicitly injected candle history.
        closed, behind = completed_intraday_bars(history, boundary, n)
        if loader is not None and not behind:
            self.intraday_refresh[symbol] = boundary
        return closed, behind

    def _price_with_meta(
        self, symbol: str, now: datetime | None = None
    ) -> tuple[float | None, datetime | None, float | None]:
        """Return the live price, timestamp, and age for one symbol.

        Raises:
            Exception: If the venue cannot read the live price.
        """
        val = self.venue.prices(symbol, self.config.exchange_of(symbol))

        if val is None:
            return None, None, None
        if isinstance(val, TickPrice):
            if not (math.isfinite(val.price) and val.price > 0):
                raise ValueError(
                    f"live quote for {symbol} must be finite and positive; got {val.price!r}"
                )
            clock_time = now or self.venue.clock()
            age = val.get_age_seconds(now=clock_time)
            return val.price, val.timestamp, age
        if isinstance(val, bool):
            raise ValueError(f"live quote for {symbol} must be numeric; got {val!r}")
        try:
            price = float(val)
        except (ValueError, TypeError) as error:
            raise ValueError(f"live quote for {symbol} is not numeric: {val!r}") from error
        if not math.isfinite(price) or price <= 0:
            raise ValueError(
                f"live quote for {symbol} must be finite and positive; got {price!r}"
            )
        clock_time = now or self.venue.clock()
        return price, clock_time, 0.0

    def _price(self, symbol: str, now: datetime | None = None) -> float | None:
        p, _, _ = self._price_with_meta(symbol, now=now)
        return p

    # ------------------------------------------------------------------
    # evaluation
    # ------------------------------------------------------------------
    def _rules(self) -> tuple[Any, Any] | None:
        """The entry/exit rules this deployment runs, or ``None`` if unresolvable."""
        try:
            resolved = self.rules_for(self.row)
        except Exception as exc:  # noqa: BLE001 — a broken store must not trade
            self.blocked_reason = f"could not resolve strategy rules: {exc}"
            self.state = RunnerDeploymentState.ERROR
            logger.exception(
                "runner %s: rule resolution failed; not trading",
                self.deployment_id[:8],
            )
            return None
        if resolved is not None:
            self.blocked_reason = None
            return resolved

        key = self.row["strategy_id"]
        self.blocked_reason = (
            f"no saved rule definition for strategy {key!r}; refusing to trade"
        )
        self.state = RunnerDeploymentState.ERROR
        logger.warning("runner %s: %s", self.deployment_id[:8], self.blocked_reason)
        return None

    def evaluate(self, now: datetime | None = None) -> list[dict[str, Any]]:
        """Run one evaluation pass. The account is folded once for the pass, not per stock."""
        self._in_pass, self._fold = True, None
        try:
            return self._evaluate(now)
        finally:
            self._in_pass, self._fold = False, None

    def _evaluate(self, now: datetime | None = None) -> list[dict[str, Any]]:
        """Run the rules over every symbol with a live price.

        Observability and freshness rules:
        - Verifies the market calendar.
        - Verifies live tick existence and age against max_tick_age_seconds.
        - Skips evaluation if ticks are stale or absent.
        - Updates deployment state (WAITING_FOR_MARKET, WAITING_FOR_TICKS, RUNNING, ERROR).
        - Records last_strategy_evaluation, last_signal, last_order, skipped counts.
        """
        eval_time = now or _now_ist()
        self._evaluation_time = eval_time
        if self.strategy_profile is not None:
            return self.strategy_profile.evaluator(self, eval_time)

        from forward.signals.rules import eval_entry, eval_exit, primary_exit

        resolved = self._rules()
        if resolved is None:
            self.state = RunnerDeploymentState.ERROR
            self.skipped_reason = self.blocked_reason or "rule resolution error"
            self.skipped_evaluations_count += 1
            self.last_pass = {
                "at": eval_time.isoformat(),
                "signals": 0,
                "orders": 0,
                "state": self.state,
                "blocked": self.blocked_reason,
            }
            return []

        if not self.calendar.is_market_open(eval_time):
            self.state = RunnerDeploymentState.WAITING_FOR_MARKET
            reason = self.calendar.reason_closed(eval_time) or "market is closed"
            self.skipped_reason = reason
            self.skipped_evaluations_count += 1
            self.last_pass = {
                "at": eval_time.isoformat(),
                "signals": 0,
                "orders": 0,
                "state": self.state,
                "skipped_reason": reason,
            }
            return []

        entry_rules, exit_rules = resolved
        opened: list[dict[str, Any]] = []
        max_age = self.venue.max_tick_age_seconds

        # Check universe prices and freshness
        valid_symbols_count = 0
        latest_tick_dt: datetime | None = None
        min_age: float | None = None

        for symbol in self.config.symbols:
            price, ts, age = self._price_with_meta(symbol, now=eval_time)
            if ts is not None and (latest_tick_dt is None or ts > latest_tick_dt):
                latest_tick_dt = ts
            if age is not None and (min_age is None or age < min_age):
                min_age = age

            if price is None:
                continue

            # Stale tick check
            if max_age is not None and age is not None and age > max_age:
                continue

            valid_symbols_count += 1

        self.last_tick_time = latest_tick_dt
        self.last_tick_age_seconds = min_age

        if valid_symbols_count == 0:
            self.state = RunnerDeploymentState.WAITING_FOR_TICKS
            why = (
                "no fresh live ticks available" if min_age is not None else "no live ticks received"
            )
            self.skipped_reason = why
            self.skipped_evaluations_count += 1
            self.last_pass = {
                "at": eval_time.isoformat(),
                "signals": 0,
                "orders": 0,
                "state": self.state,
                "skipped_reason": why,
            }
            return []

        self.state = RunnerDeploymentState.RUNNING

        self.skipped_reason = None
        self.last_strategy_evaluation = eval_time
        base_context = self._session_context(eval_time, self.calendar, entry_rules)
        candidates: list[tuple[str, float, pd.DataFrame, Any, tuple[str, str, str]]] = []

        for symbol in self.config.symbols:
            price, ts, age = self._price_with_meta(symbol, now=eval_time)
            if price is None:
                self.skipped_fills_count += 1
                continue
            if max_age is not None and age is not None and age > max_age:
                self.skipped_fills_count += 1
                continue

            position = self._position_quantity(symbol)
            if self.row["mode"] == "LIVE":
                pending = [
                    order
                    for order in self.orders.open_orders(
                        self.user_id, deployment_id=self.deployment_id
                    )
                    if order.get("deployment_id") == self.deployment_id
                    and order.get("symbol") == symbol
                ]
                if pending:
                    entry_price = self._entry_price(symbol) if position else 0
                    pnl = (price / entry_price - 1) * 100 if entry_price > 0 else 0
                    stop = getattr(exit_rules, "stop_loss_pct", None)
                    target = getattr(exit_rules, "take_profit_pct", None)
                    if position > 0 and (
                        (stop and pnl <= -abs(stop)) or (target and pnl >= abs(target))
                    ):
                        for order in pending:
                            if order["side"] == "BUY":
                                self.venue.cancel_entry(
                                    self, order, "protective exit during partial entry"
                                )
                    self.skipped_reason = "Waiting for broker order confirmation"
                    continue
            if self.config.square_off_due(eval_time):
                if position:
                    result = self._trade(
                        symbol=symbol,
                        side="SELL" if position > 0 else "BUY",
                        quantity=abs(position),
                        reason="session_square_off: saved session time",
                    )
                    if result:
                        opened.append(result)
                continue

            frame = self.live_frame(symbol)
            if frame is None or frame.empty:
                frame = pd.DataFrame(
                    {
                        "ts": [eval_time],
                        "open": [price],
                        "high": [price],
                        "low": [price],
                        "close": [price],
                        "volume": [0.0],
                    }
                )

            closed: pd.DataFrame | None = None
            behind = 0
            if self.timeframe_minutes is not None and self.config.signal_basis == "closed_bar":
                closed, behind = self._completed_intraday_frame(symbol, frame, eval_time)
                if closed is None:
                    closed = frame.iloc[:0]
            elif self.config.signal_basis == "closed_bar":
                closed, behind = self._closed_frame(symbol, eval_time, self.calendar)

            opened_at, minutes = self._session_open(symbol, price, eval_time, self.calendar)
            context = replace(base_context, open_price=opened_at, minutes_since_open=minutes)
            position = self._position_quantity(symbol)

            # --- exits first -------------------------------------------------
            if position != 0:
                entry_price = self._entry_price(symbol)
                exit_signals = eval_exit(
                    symbol,
                    frame,
                    entry_price,
                    exit_rules,
                    quantity=abs(position),
                    context=context,
                )
                if closed is not None:
                    # Protective price rules watch the live price; every other exit reads the
                    # completed bars, as in the backtest.
                    price_rules = {"stop_loss", "take_profit", "trailing_stop"}
                    same_bar = (
                        closed.empty
                        or behind > 0
                        or self.entered_bar.get(symbol) == str(closed["ts"].iloc[-1])
                    )
                    exit_signals = [x for x in exit_signals if str(x.rule) in price_rules] + (
                        []
                        if same_bar
                        else [
                            x
                            for x in eval_exit(
                                symbol,
                                closed,
                                entry_price,
                                exit_rules,
                                quantity=abs(position),
                                context=context,
                            )
                            if str(x.rule) not in price_rules
                        ]
                    )
                chosen = primary_exit(exit_signals)
                if chosen is not None:
                    self.last_signal = {
                        "symbol": symbol,
                        "rule": str(chosen.rule),
                        "reason": chosen.reason,
                        "side": "SELL" if position > 0 else "BUY",
                        "at": eval_time.isoformat(),
                    }
                    key = (symbol, str(chosen.rule), self._bar_key(symbol))
                    if key not in self.acted:
                        self.acted.add(key)
                        result = self._trade(
                            symbol=symbol,
                            side="SELL" if position > 0 else "BUY",
                            quantity=abs(position),
                            reason=f"{chosen.rule}: {chosen.reason}",
                            signal=chosen,
                        )
                        if result:
                            opened.append(result)
                    continue

            # --- entries ------------------------------------------------------
            if position != 0:
                continue
            if self.config.entry_closed(eval_time) or (closed is not None and closed.empty):
                continue
            if self._open_position_count() >= self.config.max_open_positions:
                continue

            entry_frame = frame
            if closed is not None:
                if behind > 0:
                    # Stale history: refuse to open on it rather than trade an older market.
                    self.skipped_evaluations_count += 1
                    self.skipped_reason = f"price history is behind the latest completed {self.config.timeframe} bar, so new entries are paused"
                    continue
                entry_frame = closed
            signals = eval_entry(symbol, entry_frame, entry_rules, context)
            if not signals:
                continue
            chosen = signals[0]
            self.last_signal = {
                "symbol": symbol,
                "rule": str(chosen.rule),
                "reason": chosen.reason,
                "side": "BUY",
                "at": eval_time.isoformat(),
            }
            key = (symbol, str(chosen.rule), self._bar_key(symbol))
            if key in self.acted:
                continue
            candidates.append((symbol, price, entry_frame, chosen, key))

        # Entries are placed after the whole universe has been looked at, best first, so
        # a full book is filled by the strongest candidates rather than the first ones
        # the loop happened to reach. Exits above have already freed their slots.
        #
        # `rank_formula` (see `EntryRules`) is what turns "these symbols passed
        # the entry rule" into "buy the strongest N in the universe" — a
        # cross-symbol strategy, built from the same per-symbol formula engine
        # rather than a new unsafe grammar: every candidate already qualified
        # on its own, this only decides the fill order. Without it, the
        # original ordering (a gap-down strategy's own gap size) is preserved.
        if entry_rules.rank_formula:
            from forward.signals.formula import FormulaError

            try:
                candidates = prioritize_entry_candidates(candidates, entry_rules.rank_formula)
            except FormulaError as exc:
                # An invalid ranking rule must not silently change which symbols
                # this strategy buys. Exits were already processed above, so only
                # skip new entries for this pass.
                self.state = RunnerDeploymentState.ERROR
                self.blocked_reason = f"invalid entry ranking formula: {exc}"
                self.skipped_reason = self.blocked_reason
                self.skipped_evaluations_count += 1
                logger.error("runner %s: %s", self.deployment_id[:8], self.blocked_reason)
                return opened
        else:
            candidates = prioritize_entry_candidates(candidates, entry_rules.rank_formula)
        for symbol, price, frame, chosen, key in candidates:
            if self._open_position_count() >= self.config.max_open_positions:
                break
            self.acted.add(key)
            try:
                quantity = self._size(price, symbol=symbol, frame=frame, entry_rules=entry_rules)
            except (KeyError, TypeError, ValueError) as exc:
                self.state = RunnerDeploymentState.ERROR
                self.blocked_reason = f"invalid position sizing configuration: {exc}"
                self.skipped_reason = self.blocked_reason
                self.skipped_evaluations_count += 1
                logger.error("runner %s: %s", self.deployment_id[:8], self.blocked_reason)
                return opened
            if quantity <= 0:
                continue
            result = self._trade(
                symbol=symbol,
                side="BUY",
                quantity=quantity,
                reason=f"{chosen.rule}: {chosen.reason}",
                signal=chosen,
                sizing=self.last_sizing,
            )
            if result:
                opened.append(result)
                if "ts" in frame.columns and len(frame):
                    self.entered_bar[symbol] = str(frame["ts"].iloc[-1])

        if not opened and self.skipped_reason is None:
            self.skipped_reason = "Strategy produced no signal"

        return opened

    def _session_context(self, when: datetime, cal: Any, entry_rules: Any) -> Any:
        """What today's session adds to the rules. The market reading is read only when asked for."""
        wants_market = getattr(entry_rules, "gap_market_min_pct", None) is not None
        return build_context(
            when,
            cal,
            self._market_week if wants_market else None,
            open_price=None,
            minutes_since_open=None,
        )

    def _session_open(
        self, symbol: str, price: float, when: datetime, cal: Any
    ) -> tuple[float | None, float | None]:
        """The session's first fresh price for a symbol, and how long after 09:15 it was seen.

        The tick feed carries the last traded price and nothing else, so the open is taken
        as the first price this loop sees each day. A loop that starts late sees a later
        price and a large delay; the gap rule then declines rather than call it the open.
        """
        local = when.astimezone(IST) if when.tzinfo else when.replace(tzinfo=IST)
        today = local.date()
        true_open = self._tick_open(symbol)
        if true_open is not None:
            # The feed reports the session's real opening price, so no guessing from the first
            # price this loop happened to see, and no "late start" penalty.
            return true_open, 0.0
        seen = self._first_price.get(symbol)
        if seen is None or seen[0] != today:
            start, _ = cal.session_bounds(today)
            minutes = max(0.0, (local - start).total_seconds() / 60.0)
            seen = (today, price, minutes)
            self._first_price[symbol] = seen
        return seen[1], seen[2]

    def _tick_open(self, symbol: str) -> float | None:
        """The session's real open from the live tick, when the feed supplies one."""
        try:
            val = self.venue.prices(symbol, self.config.exchange_of(symbol))
        except Exception:  # noqa: BLE001 - no tick, no open
            return None
        open_px = getattr(val, "open", None)
        return float(open_px) if open_px and float(open_px) > 0 else None

    def _bar_key(self, symbol: str) -> str:
        """A key that changes once per bar, so a rule fires once per bar.

        The daily rules are daily: a breakout that is true at 10:00 is still true
        at 14:00, and acting on it twice is acting on one signal twice. In
        intraday mode a "bar" is `timeframe_minutes` wide instead of a whole
        session — a rule true throughout one 5-minute bar must not fire five
        times on five ticks, but a *fresh* setup on the next bar is a new
        signal, not the same one re-detected.
        """
        if self.strategy_profile and self.strategy_profile.signal_key_builder:
            return self.strategy_profile.signal_key_builder(self, symbol)
        n = self.timeframe_minutes
        if n is None:
            return f"{symbol}:{(getattr(self, '_evaluation_time', None) or datetime.now(IST)).date().isoformat()}"
        bar = self.intraday_bar.get(symbol)
        start = (
            bar["start"]
            if bar is not None
            else (getattr(self, "_evaluation_time", None) or datetime.now(IST))
        )
        return f"{symbol}:{start.isoformat()}"

    def _size(
        self,
        price: float,
        symbol: str | None = None,
        frame: pd.DataFrame | None = None,
        entry_rules: Any = None,
    ) -> int:
        """How many shares a signal buys, calculated via PositionSizingEngine."""
        if not math.isfinite(price) or price <= 0:
            raise ValueError("entry price must be finite and positive for position sizing")

        portfolio = self._portfolio()
        capital = float(self.row["capital"])
        available_capital = float(portfolio.cash)

        from forward.strategy.sizing import PositionSizingEngine, SizingConfig, SizingMethod

        raw_sizing = self.config.sizing
        if raw_sizing is None:
            raise ValueError("an explicit sizing block is required")
        sizing_cfg = SizingConfig.from_dict(raw_sizing)

        # Extract stop loss
        stop_loss_pct = self.config.stop_loss_pct
        if stop_loss_pct is None and entry_rules is not None:
            stop_loss_pct = getattr(entry_rules, "stop_loss_pct", None)

        # Extract ATR
        atr_val: float | None = None
        if sizing_cfg.method is SizingMethod.ATR_VOLATILITY_SIZING:
            if frame is None or len(frame) < 15:
                raise ValueError("ATR sizing requires at least 15 completed bars")
            missing = {"high", "low", "close"} - set(frame.columns)
            if missing:
                raise ValueError(
                    f"ATR sizing requires OHLC columns: {', '.join(sorted(missing))}"
                )
            from forward.strategy.indicators import atr as calc_atr

            atr_series = calc_atr(frame["high"], frame["low"], frame["close"])
            atr_val = float(atr_series.iloc[-1])
            if not math.isfinite(atr_val) or atr_val <= 0:
                raise ValueError("ATR sizing has no valid ATR for the latest bar")

        current_stock_exposure = 0.0
        multiplier = 1.0
        if symbol:
            pos = portfolio.position(symbol)
            instrument = getattr(pos, "instrument", None) if pos else None
            if instrument is not None and getattr(instrument, "multiplier", None):
                multiplier = float(instrument.multiplier)
            if pos and not pos.is_flat:
                current_stock_exposure = abs(float(pos.quantity) * price * multiplier)

        result = PositionSizingEngine.calculate(
            sizing_cfg,
            entry_price=price,
            capital=capital,
            available_capital=available_capital,
            stop_loss_pct=stop_loss_pct,
            atr=atr_val,
            multiplier=multiplier,
            current_stock_exposure=current_stock_exposure,
            current_portfolio_exposure=float(portfolio.gross_exposure),
        )

        res_dict = result.as_dict()
        self.last_sizing = res_dict
        return result.final_quantity

    # ------------------------------------------------------------------
    # the fold
    # ------------------------------------------------------------------
    def _portfolio(self) -> Any:
        """The deployment's account, folded from order_events.

        Rebuilt on every call, except inside one evaluation pass: there it is built once
        and rebuilt only after a trade, because a pass asks for it twice per stock and a
        thousand stocks made that the slowest part of the loop.
        """
        if self._in_pass:
            if self._fold is None:
                self._fold = self._fold_now()
            return self._fold
        return self._fold_now()

    def _fold_now(self) -> Any:
        return self.ledger.portfolio(
            self.user_id,
            deployment_id=self.deployment_id,
            initial_cash=float(self.row["capital"]),
            prices=self.live_prices(),
            mode=str(self.row["mode"]).upper(),
        )

    def live_prices(self) -> dict[str, float]:
        """A live mark for every symbol this deployment knows about."""
        out: dict[str, float] = {}
        for symbol in self.config.symbols:
            price = self._price(symbol)
            if price is not None:
                out[symbol] = price
        return out

    def _position_quantity(self, symbol: str) -> float:
        position = self._portfolio().position(symbol)
        return float(position.quantity) if position is not None else 0.0

    def _entry_price(self, symbol: str) -> float:
        position = self._portfolio().position(symbol)
        if position is None:
            return 0.0
        price = float(position.avg_price)
        if price <= 0:
            raise ValueError(f"open position {symbol!r} has no positive average price")
        return price

    def _open_position_count(self) -> int:
        """How many positions this deployment holds, across its whole book.

        **Every non-flat position, not only the configured universe.** The
        previous version iterated ``config.symbols``, which made the count depend
        on a list that is editable while the deployment runs: drop a symbol from
        the universe while holding it and the position becomes invisible to
        ``max_open_positions``, so the deployment can open its way past the cap
        while the risk surface reports the limit as enforced. A position consumes
        capital whether or not the loop still evaluates its symbol, so it is
        counted.

        The fold is the source, not a counter held here — see the module
        docstring.
        """
        portfolio = self._portfolio()
        occupied = {
            symbol for symbol, position in portfolio.positions.items() if not position.is_flat
        }
        if self.row["mode"] == "LIVE":
            occupied.update(
                order["symbol"]
                for order in self.orders.open_orders(self.user_id, deployment_id=self.deployment_id)
                if order.get("deployment_id") == self.deployment_id and order["side"] == "BUY"
            )
        return len(occupied)

    # ------------------------------------------------------------------
    # trading
    # ------------------------------------------------------------------
    def _trade(
        self,
        *,
        symbol: str,
        side: str,
        quantity: float,
        reason: str,
        signal: Any = None,
        sizing: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Route one signal through the shared path: risk, OMS, venue, fill.

        ``ExecutionService.place`` is the whole pipeline — open → risk → submit →
        venue → record fill — so this method deliberately contains no decision
        of its own beyond *whether* to send.
        """
        price = self._price(symbol)
        draft = OrderDraft(
            user_id=self.user_id,
            symbol=symbol,
            side=side,
            quantity=float(quantity),
            mode=str(self.row["mode"]).upper(),
            product=self.config.product,
            exchange=self.config.exchange_of(symbol),
            order_type=OrderType.MARKET.value,
            requested_price=price,
            deployment_id=self.deployment_id,
            strategy_id=self.row["strategy_id"],
            strategy_version=self.row["strategy_version"],
            # The bar this signal belongs to, so the order is traceable back to
            # the context recorded against the same bar (see
            # ``_record_signal_context``). Idempotency is unchanged: the key is
            # still derived independently in ``_idempotency_key``.
            signal_id=f"{self.deployment_id}:{self._bar_key(symbol)}",
            tag="strategy-runner",
            # The rule's own words, carried onto the order's NEW event. This is
            # what lets the monitoring timeline answer "why did this order
            # exist?" instead of showing a fill with no antecedent.
            signal_reason=reason,
        )
        # A signal-driven order gets a key, so a retry after a dropped response
        # returns the existing order rather than placing a second one. It is
        # derived from the bar, so re-evaluating the same bar is a no-op.
        key = self._idempotency_key(draft, symbol, side)

        now_iso = utcnow().isoformat()
        try:
            result = self.execution.place(draft, idempotency_key=key)
            self._fold = None  # the account changed: the next look must not use the old fold
        except Exception as exc:  # noqa: BLE001 - one bad order must not stop the loop
            self._fold = None
            logger.exception(
                "runner %s: placing %s %s failed",
                self.deployment_id[:8],
                side,
                symbol,
            )
            self.skipped_fills_count += 1
            self.skipped_reason = f"System error: {exc}"
            self.last_risk_decision = {
                "symbol": symbol,
                "side": side,
                "quantity": float(quantity),
                "approved": False,
                "reason": f"System error placing order: {exc}",
                "rule": reason,
                "at": now_iso,
            }
            return None

        logger.info(
            "runner %s: %s %s x%s -> %s (%s)",
            self.deployment_id[:8],
            side,
            symbol,
            int(quantity),
            result.status,
            reason,
        )
        order_info = {
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "status": result.status,
            "order_id": result.order_id,
            "reason": reason,
            "sizing": sizing or self.last_sizing,
        }
        self.last_order = order_info

        # Track risk decision and fill state
        is_risk_rejected = (result.status == "REJECTED") and (
            not getattr(result, "broker_order_id", None)
            or "risk" in (result.reject_reason or "").lower()
            or "limit" in (result.reject_reason or "").lower()
            or "exposure" in (result.reject_reason or "").lower()
        )
        if is_risk_rejected:
            self.last_risk_decision = {
                "symbol": symbol,
                "side": side,
                "quantity": float(quantity),
                "approved": False,
                "reason": result.reject_reason or "Rejected by risk engine",
                "rule": reason,
                "at": now_iso,
            }
            self.skipped_fills_count += 1
            self.skipped_reason = (
                f"Risk rejected signal: {result.reject_reason or 'Risk limit exceeded'}"
            )
        else:
            self.last_risk_decision = {
                "symbol": symbol,
                "side": side,
                "quantity": float(quantity),
                "approved": True,
                "reason": "Approved by risk engine",
                "rule": reason,
                "at": now_iso,
            }
            if result.status == "REJECTED":
                self.skipped_fills_count += 1
                self.skipped_reason = (
                    f"Order rejected: {result.reject_reason or 'Venue rejected order'}"
                )
            elif result.status == "FILLED":
                self.last_fill = order_info
                self.skipped_reason = "Paper fill completed"
            else:
                self.skipped_reason = "Order waiting for fill"
        self._record_signal_context(symbol=symbol, side=side)
        return order_info

    def _record_signal_context(self, *, symbol: str, side: str) -> None:
        """Annotate the just-raised signal with its market/sector/stock context.

        Best-effort and strictly post-hoc: it runs *after* the order has been
        placed (so a slow market scan can never delay or block a signal), and any
        failure is swallowed — the context record is an annotation, never a gate.

        The ``signal_id`` is the bar key, matching ``OrderDraft.signal_id``, so
        the recorded context and the order agree on the signal they describe and
        the analytics layer can resolve one against the other.
        """
        try:
            self._signal_context.enrich_live(
                user_id=self.user_id,
                symbol=symbol,
                action="SELL" if str(side).upper() in ("SELL", "S") else "BUY",
                signal_ts=utcnow().isoformat(),
                signal_id=self._bar_key(symbol),
                strategy_id=self.row["strategy_id"],
                strategy_version=self.row["strategy_version"],
                signal_source="PAPER",
            )
        except Exception as exc:  # noqa: BLE001 — context is not a gate
            logger.debug(
                "runner %s: signal context for %s skipped: %s",
                self.deployment_id[:8],
                symbol,
                exc,
            )

    def _idempotency_key(self, draft: OrderDraft, symbol: str, side: str) -> str:
        """One key per (deployment, bar, symbol, side).

        The bar is passed as the ``signal_id`` rather than bolted on as an extra
        component: ``idempotency_key_for`` is the single definition of what an
        intent's identity is, and a runner that appended its own field would be
        deriving a key the rest of the platform could not reproduce. A daily bar
        key is exactly the right granularity — the rules are daily, so one signal
        per symbol per session is one intent.
        """
        from forward.execution.oms import idempotency_key_for

        return idempotency_key_for(
            user_id=self.user_id,
            strategy_id=draft.strategy_id,
            strategy_version=draft.strategy_version,
            signal_id=f"{self.deployment_id}:{self._bar_key(symbol)}",
            symbol=symbol,
            side=draft.side,
            leg_index=0,
        )

    # ------------------------------------------------------------------
    # resting orders
    # ------------------------------------------------------------------
    def poll_resting_orders(self, now: datetime | None = None) -> list[dict[str, Any]]:
        """Re-evaluate every open paper order against the current price.

        This is requirement 4, and it is the difference between a paper LIMIT
        order and a promise: without it a limit that opened away from the market
        sits in the book forever, never filling and never cancelling, while the
        dashboard shows it as live.
        """
        from forward.execution.venue import VENUE_FILLED as _FILLED
        from forward.execution.venue import VENUE_PARTIAL
        from forward.execution.venue import VENUE_REJECTED as _REJECTED

        if self.row["mode"] == "LIVE":
            return self.venue.sync(self, now or _now_ist())

        filled: list[dict[str, Any]] = []

        open_orders = [
            order
            for order in self.orders.open_orders(self.user_id, deployment_id=self.deployment_id)
            if order.get("deployment_id") == self.deployment_id
            and order.get("mode") == "PAPER"
            # Only orders the venue has accepted and that are resting. An order
            # still in NEW or RISK_APPROVED has not reached the venue yet, and a
            # SUBMITTED one whose outcome we never learned is not ours to retry.
            and order.get("status") in ("ACKNOWLEDGED", "PARTIALLY_FILLED")
        ]
        if not open_orders:
            return filled

        if self.strategy_profile and self.strategy_profile.refuse_resting_fills:
            return filled  # Durable IOC recovery must cancel, never fill later.

        for order in open_orders:
            if (
                self.strategy_profile
                and self.strategy_profile.cancel_resting_entries
                and str(order.get("signal_id") or "").endswith(":entry")
            ):
                self.orders.request_cancel(
                    order["order_id"], self.user_id, reason="Opening entry is immediate-or-cancel"
                )
                self.orders.confirm_cancel(
                    order["order_id"],
                    self.user_id,
                    raw={"reason": "Opening entry remainder cancelled"},
                )
                continue
            if (
                not (self.strategy_profile and self.strategy_profile.cancel_resting_entries)
                and self.config.entry_closed(now or _now_ist())
                and order.get("side") == "BUY"
            ):
                self.orders.request_cancel(
                    order["order_id"], self.user_id, reason="entry cutoff reached"
                )
                self.orders.confirm_cancel(
                    order["order_id"], self.user_id, raw={"reason": "entry cutoff reached"}
                )
                continue
            price = self._price(order["symbol"])
            if price is None:
                continue
            try:
                outcome = self.venue.match(order)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "runner %s: match failed for %s", self.deployment_id[:8], order["order_id"]
                )
                continue

            if outcome.status == _REJECTED:
                # A resting order that became unpriceable is rejected rather than
                # left in the book looking live.
                self.orders.reject(
                    order["order_id"],
                    self.user_id,
                    reason=outcome.reject_reason or "unpriceable while resting",
                    source="oms",
                    raw=outcome.raw,
                )
                continue

            if outcome.status not in (_FILLED, VENUE_PARTIAL) or outcome.filled_qty <= 0:
                continue

            try:
                self.orders.record_fill(
                    order["order_id"],
                    self.user_id,
                    filled_qty=outcome.filled_qty,
                    filled_price=outcome.filled_price or price,
                    commission=outcome.commission,
                    raw=outcome.raw,
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "runner %s: recording fill failed for %s",
                    self.deployment_id[:8],
                    order["order_id"],
                )
                continue

            filled.append(
                {
                    "order_id": order["order_id"],
                    "symbol": order["symbol"],
                    "filled_qty": outcome.filled_qty,
                    "filled_price": outcome.filled_price,
                }
            )
        return filled

    # ------------------------------------------------------------------
    # observation
    # ------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        """The deployment's current account, for the API and the UI."""
        prices = self.live_prices()
        return self.ledger.snapshot(
            self.user_id,
            deployment_id=self.deployment_id,
            initial_cash=float(self.row["capital"]),
            prices=prices,
        )
