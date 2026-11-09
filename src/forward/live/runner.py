"""Run a live strategy through injected feed and broker ports.

The runner uses the same Strategy, Portfolio, and Risk objects as backtests:

    warmup history -> strategy.prepare() -> live bars -> strategy.on_bar()
                                                     -> risk -> broker

Warmup matters: indicators computed on a short window would otherwise produce
nonsense signals for the first N bars of the session.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime
from datetime import time as dtime

import pandas as pd
from loguru import logger

from forward.core.instrument_models import Instrument
from forward.core.live_broker_ports import LiveBrokerPort
from forward.core.live_feed_ports import LiveRunnerFeedPort, LiveWarmupHistoryPort
from forward.core.session import IST
from forward.execution.portfolio import Portfolio
from forward.execution.risk import RiskEngine, RiskLimits
from forward.strategy.base import Strategy, StrategyContext

OHLCV = ["open", "high", "low", "close", "volume"]


@dataclass
class LiveConfig:
    freq: str = "1min"
    poll_timeout: float = 1.0
    square_off_time: dtime = dtime(15, 15)
    #: Bars of history fetched before the first live bar, so indicators are
    #: valid at the open. 0 skips warmup, leaving indicators NaN until enough
    #: live bars accumulate.
    warmup_bars: int = 500
    #: Hard cap on retained bars per symbol — bounds memory over a long session.
    max_history_bars: int = 2_000
    max_runtime_seconds: float | None = None
    flatten_on_stop: bool = True
    #: Some strategies manage a manual, multi-session exit; they can disable the
    #: default intraday square-off while retaining stop-time flattening.
    square_off_enabled: bool = True
    #: Reject bars older than this (seconds) instead of trading them. The
    #: snapshot queue can hold a backlog after a disconnect; trading it
    #: would act on a market that no longer exists. None disables.
    max_snapshot_age_seconds: float | None = 300.0


class LiveRunner:
    """Run a strategy on live market snapshots and route its orders."""

    def __init__(
        self,
        instruments: dict[str, Instrument],
        strategy: Strategy,
        broker: LiveBrokerPort,
        feed: LiveRunnerFeedPort,
        warmup_frames: dict[str, pd.DataFrame] | None = None,
        config: LiveConfig | None = None,
        risk: RiskLimits | None = None,
        initial_cash: float = 1_000_000.0,
        warmup_history: LiveWarmupHistoryPort | None = None,
        kill_switch_provider=None,
        contract_catalog=None,
    ) -> None:
        """Create a runner with explicit market and execution adapters.

        Args:
            instruments: Contracts watched by the strategy.
            strategy: Strategy evaluated for each complete market snapshot.
            broker: Broker port used for orders and fill updates.
            feed: Live feed port used for snapshots, quotes, and subscriptions.
            warmup_frames: Historical frames already loaded for the strategy.
            config: Live loop settings.
            risk: Limits checked before every order.
            initial_cash: Starting portfolio cash.
            warmup_history: Adapter used when the strategy needs historical bars.
            kill_switch_provider: Reads the durable platform kill switch.
            contract_catalog: Option catalog supplied to strategy context.
        """
        self.instruments = instruments
        self.strategy = strategy
        self.broker = broker
        self.config = config if config is not None else LiveConfig()
        self.portfolio = Portfolio(initial_cash=initial_cash)
        self.risk = RiskEngine(risk or RiskLimits())
        self.risk.reset_daily(self.portfolio.equity)
        #: Zero-argument callable returning True while the durable kill
        #: switch is engaged. The static ``RiskLimits`` above are a snapshot;
        #: without a live read of the switch, engaging it stops paper but not
        #: a running live loop. Wire ``forward.services.risk`` (or any store
        #: reader) here. A provider failure fails closed — an unknown safety
        #: state must not keep trading.
        self.kill_switch_provider = kill_switch_provider
        self._seen_fill_keys: set[tuple] = set()
        self._stopped_for_kill_switch = False
        self.frames: dict[str, pd.DataFrame] = (
            warmup_frames if warmup_frames is not None else {}
        )
        self.feed = feed
        self._warmup_history = warmup_history
        if (
            self.config.warmup_bars > 0
            and not any(not frame.empty for frame in self.frames.values())
            and self._warmup_history is None
        ):
            raise ValueError("live warmup history adapter is required")
        self.trades: list = []
        self._stop = False
        self._squared_off_date: datetime.date | None = None
        self._risk_date: date | None = None
        self._indicator_len = -1
        self.contract_catalog = contract_catalog

    # ------------------------------------------------------------------
    @staticmethod
    def _new_frame() -> pd.DataFrame:
        return pd.DataFrame(columns=OHLCV)

    def _length(self) -> int:
        return len(next(iter(self.frames.values()))) if self.frames else 0

    def _ensure_frames(self) -> None:
        for symbol in self.instruments:
            self.frames.setdefault(symbol, self._new_frame())

    def load_warmup(self) -> None:
        """Fetch recent candles so indicators are valid from the first live bar.

        Without this, ``prepare()`` runs against an empty frame, every indicator
        stays NaN, and the strategy never signals at all — a silent no-op rather
        than a visible failure.
        """
        bars = self.config.warmup_bars
        if bars <= 0 or any(not f.empty for f in self.frames.values()):
            return
        if self._warmup_history is None:
            raise RuntimeError("live warmup history adapter is required")
        frame = self._warmup_history.fetch(
            self.instruments, self.config.freq, bars
        )
        if frame.empty:
            raise ValueError("warmup history returned no candles")

        index = pd.DatetimeIndex(sorted(frame["ts"].unique()))
        for symbol in self.instruments:
            sub = frame[frame["symbol"] == symbol].set_index("ts")
            self.frames[symbol] = sub.reindex(index)[OHLCV].tail(bars)
        logger.info(
            "warmup: {} bars x {} symbols ({} -> {})",
            self._length(), len(self.instruments), index[0], index[-1],
        )

    def _append_snapshot(self, snapshot) -> None:
        """Append one row per instrument, NaN where the symbol had no bar.

        Aligning every symbol onto the same grid is what the backtester does
        (``BacktestEngine._build_frames``), and it is what makes a single
        ``ctx.index`` meaningful — with ragged frames, one index cannot address
        them all and ``ctx.row()`` silently returns the wrong bar.
        """
        index = pd.DatetimeIndex([snapshot.ts])
        for symbol in self.instruments:
            bar = snapshot.bars.get(symbol)
            values = (
                [[bar.open, bar.high, bar.low, bar.close, bar.volume]]
                if bar is not None
                else [[float("nan")] * len(OHLCV)]
            )
            row = pd.DataFrame(values, index=index, columns=OHLCV)
            self.frames[symbol] = pd.concat([self.frames[symbol], row])
        self._trim()

    def _trim(self) -> None:
        cap = self.config.max_history_bars
        if cap <= 0:
            return
        for symbol, frame in self.frames.items():
            if len(frame) > cap:
                self.frames[symbol] = frame.iloc[-cap:]

    def _refresh_indicators(self) -> None:
        """Re-run ``prepare()`` so the newest bar has indicator values.

        ``prepare()`` is a one-shot vectorised pass over a static frame in
        backtests. Live frames grow, so calling it once at startup leaves every
        subsequent bar with NaN indicators — which is why this runner could
        never actually trade.
        """
        length = self._length()
        if length == self._indicator_len:
            return
        self.strategy.prepare(self.frames)
        self._indicator_len = length

    # ------------------------------------------------------------------
    def _kill_switch_engaged(self) -> bool:
        if self.risk.limits.kill_switch:
            return True
        if self.kill_switch_provider is None:
            return False
        try:
            return bool(self.kill_switch_provider())
        except Exception as exc:  # noqa: BLE001 - unknown safety state: stop
            logger.error("kill-switch read failed ({}); failing closed", exc)
            return True

    def _submit(self, order):
        kill_switch_engaged = (
            self._stopped_for_kill_switch or self._kill_switch_engaged()
        )
        if kill_switch_engaged:
            logger.warning("order blocked: kill switch engaged")
            order.reject_reason = "kill switch engaged"
            return order
        verdict = self.risk.check_order(order, self.portfolio)
        if not verdict.allowed:
            logger.warning("order blocked by risk: {}", verdict.reason)
            order.reject_reason = verdict.reason
            return order
        logger.info(
            "submitting {} {} {} @ {}",
            order.side.name,
            order.quantity,
            order.instrument.symbol,
            order.order_type.value,
        )
        try:
            return self.broker.place_order(order)
        except Exception as exc:  # noqa: BLE001 - one rejection ends no session
            logger.error("broker rejected {}: {}; continuing", order.order_id, exc)
            order.reject_reason = str(exc)
            return order

    def _sync_broker_fills(self) -> int:
        """Fold unseen broker trades into the local book; return how many.

        The local portfolio only learns fills here. Without this the book
        stays flat forever and ``close_all()`` — which reads local positions
        — finds nothing to close, so risk-halt and square-off cancel resting
        orders and then close nothing while logging success.
        """
        fills = self.broker.fills_from_trades()
        applied = 0
        for fill in fills:
            key = (fill.order_id, fill.instrument.symbol, fill.quantity,
                   fill.price, str(fill.ts))
            if key in self._seen_fill_keys:
                continue
            self._seen_fill_keys.add(key)
            self.portfolio.apply_fill(fill)
            applied += 1
        if applied:
            logger.info("synced {} broker fill(s) into the local book", applied)
        return applied

    # ------------------------------------------------------------------
    def run(self) -> None:
        self.load_warmup()
        self._ensure_frames()
        self._refresh_indicators()
        ctx = StrategyContext(
            instruments=self.instruments,
            frames=self.frames,
            submit=self._submit,
            portfolio_getter=lambda: self.portfolio,
            contract_catalog=self.contract_catalog,
            watch_contract=self._watch_contract,
            quote_getter=self.feed.last_tick,
            stop_requester=self.stop,
        )
        started = time.time()

        try:
            self.feed.start()
            self.strategy.on_start(ctx)
            logger.info("live runner started on {}", list(self.instruments))
            while not self._stop:
                snapshot = self.feed.get(timeout=self.config.poll_timeout)
                if snapshot is None:
                    if (
                        self.config.max_runtime_seconds
                        and time.time() - started > self.config.max_runtime_seconds
                    ):
                        break
                    continue

                if self._snapshot_stale(snapshot.ts):
                    logger.warning("skipping stale snapshot ({}); feed may be down", snapshot.ts)
                    continue
                now = snapshot.ts
                if self._risk_date != now.date():
                    self.risk.reset_daily(self.portfolio.equity)
                    self._risk_date = now.date()
                self._append_snapshot(snapshot)
                self._refresh_indicators()
                for symbol, bar in snapshot.bars.items():
                    position = self.portfolio.position(symbol)
                    if not position.is_flat:
                        position.mark(bar.close, bar.ts)

                prices = {s: b.close for s, b in snapshot.bars.items()}
                self.portfolio.mark(now, prices)
                self._sync_broker_fills()

                if self._kill_switch_engaged():
                    self._stopped_for_kill_switch = True
                    logger.error("kill switch engaged — runner stopped; positions need manual closure")
                    break

                verdict = self.risk.check(self.portfolio, now)
                if not verdict.allowed:
                    logger.error("risk halt: {} — flattening", verdict.reason)
                    self.flatten(ctx)
                    break

                # Mandatory intraday square-off.
                if (
                    self.config.square_off_enabled
                    and now.time() >= self.config.square_off_time
                    and self._squared_off_date != now.date()
                ):
                    self._squared_off_date = now.date()
                    logger.info("square-off time reached")
                    self.flatten(ctx)

                ctx.now = now
                ctx.bars = snapshot.bars
                ctx.index = max(self._length() - 1, 0)
                for symbol, bar in snapshot.bars.items():
                    ctx.windows[symbol].push(bar)
                self.strategy.on_bar(ctx)

                if (
                    self.config.max_runtime_seconds
                    and time.time() - started > self.config.max_runtime_seconds
                ):
                    break
        except KeyboardInterrupt:
            logger.warning("interrupted")
        finally:
            try:
                if (
                    self.config.flatten_on_stop
                    and not self._stopped_for_kill_switch
                    and not self._kill_switch_engaged()
                ):
                    self.flatten(ctx)
            finally:
                try:
                    self.strategy.on_stop(ctx)
                finally:
                    self.feed.stop()
                    logger.info("live runner stopped")

    # ------------------------------------------------------------------
    def _snapshot_stale(self, ts) -> bool:
        """True when a snapshot is too old to trade.

        The snapshot timestamp must be a datetime or an ISO string. Invalid or
        missing timestamps raise an error so the runner cannot trade using an
        unknown market-data age.

        Args:
            ts: Timestamp attached to the market snapshot.

        Returns:
            Whether the snapshot is older than the configured maximum age.

        Raises:
            TypeError: The timestamp is not a datetime or string.
            ValueError: The timestamp string is invalid.
        """
        limit = self.config.max_snapshot_age_seconds
        if limit is None:
            return False
        moment = self._as_aware(ts)
        if moment is None:
            raise ValueError(f"snapshot has an invalid timestamp: {ts!r}")
        now = datetime.now(IST)
        return (now - moment).total_seconds() > limit

    @staticmethod
    def _as_aware(ts) -> datetime:
        if isinstance(ts, datetime):
            moment = ts
        elif isinstance(ts, str):
            try:
                moment = datetime.fromisoformat(ts)
            except ValueError as exc:
                raise ValueError(f"snapshot has an invalid timestamp: {ts!r}") from exc
        else:
            raise TypeError(f"snapshot timestamp must be a datetime or ISO string: {ts!r}")
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=IST)
        return moment

    def flatten(self, ctx: StrategyContext) -> None:
        # Sync first: the local book only learns fills here, and closing
        # against a stale-flat book submits nothing while reporting success.
        self._sync_broker_fills()
        # Cancel first, close second — same reasoning as the backtest engine's
        # halt handling: a resting stop/limit order (an entry still working,
        # a protective stop placed directly rather than through the sizing
        # wrapper) needs clearing before the flatten's own closing orders go
        # out, or a broker whose cancel-all has no notion of "just submitted"
        # could cancel the very orders meant to flatten the book.
        cancelled = self.broker.cancel_all()
        if cancelled:
            logger.info("cancelled {} resting order(s) on flatten", cancelled)
        for order in ctx.close_all():
            logger.info("flatten order submitted: {}", order.order_id)

    def stop(self) -> None:
        """Ask the live runner to stop after its current strategy pass."""
        self._stop = True

    def _watch_contract(self, instrument: Instrument) -> None:
        """Subscribe to a newly selected contract and add its live history frame."""
        if instrument.symbol in self.instruments:
            return
        self.feed.subscribe_instruments([instrument])
        self.instruments[instrument.symbol] = instrument
        self.frames[instrument.symbol] = self._new_frame()
