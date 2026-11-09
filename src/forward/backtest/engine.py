"""Event-driven backtest engine.

Loop per bar:
  1. advance the simulated broker (fills resting/queued orders at this bar)
  2. mark the portfolio to this bar's closes
  3. run pre-trade risk
  4. let the strategy submit orders (they fill from the *next* bar)

That ordering is what prevents look-ahead: a strategy acting on bar *i* can
never be filled at bar *i*'s close.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd
from loguru import logger

from forward.backtest.broker import SimulatedBroker
from forward.backtest.metrics import Metrics, compute_metrics
from forward.backtest.trades import TradeLog
from forward.config.costs import commission_model, slippage_model
from forward.core.backtest_ports import BacktestFeedPort
from forward.core.costs import CommissionModel, SlippageModel
from forward.core.enums import OrderStatus
from forward.core.instrument_models import Instrument
from forward.core.market_models import MarketSnapshot
from forward.core.order_models import Order
from forward.execution.portfolio import Portfolio
from forward.execution.risk import RiskEngine, RiskLimits
from forward.strategy.base import Strategy, StrategyContext


@dataclass
class BacktestConfig:
    initial_cash: float = 1_000_000.0
    commission: CommissionModel = field(default_factory=commission_model)
    slippage: SlippageModel = field(default_factory=slippage_model)
    fill_on_next_open: bool = True
    participation_rate: float = 1.0
    equity_margin_ratio: float = 1.0
    #: Disable only when the run is explicitly P&L-only and margin is unknown.
    enforce_margin: bool = True
    allow_short: bool = True
    risk_free_rate: float = 0.0
    square_off_eod: bool = False
    entry_cutoff: str = "14:45"
    square_off_time: str = "15:15"
    risk: RiskLimits | None = None
    assert_invariants: bool = True
    #: Bars to run without letting the strategy act, so indicators can warm up
    #: before scoring begins. Frames and portfolio marks are still built, so a
    #: strategy that needs 200 bars of history can be evaluated over a test
    #: window shorter than that — otherwise it simply never trades, and "no
    #: trades" is indistinguishable from "no edge".
    warmup_bars: int = 0


@dataclass
class BacktestResult:
    metrics: Metrics
    equity: pd.Series
    cash: pd.Series
    exposure: pd.Series
    trades: pd.DataFrame
    fills: pd.DataFrame
    orders: pd.DataFrame
    strategy_name: str
    killed: bool = False
    kill_reason: str | None = None

    def summary(self) -> str:
        header = f"=== {self.strategy_name} ==="
        if self.killed:
            header += f"\n!! halted by risk: {self.kill_reason}"
        return f"{header}\n{self.metrics.summary()}"

    @property
    def total_return(self) -> float:
        return self.metrics.total_return_pct


class BacktestEngine:
    def __init__(
        self,
        feed: BacktestFeedPort,
        strategy: Strategy,
        config: BacktestConfig | None = None,
    ) -> None:
        self.feed = feed
        self.strategy = strategy
        self.config = config or BacktestConfig()
        self.portfolio = Portfolio(
            initial_cash=self.config.initial_cash,
            equity_margin_ratio=self.config.equity_margin_ratio,
            allow_short=self.config.allow_short,
        )
        self.broker = SimulatedBroker(
            portfolio=self.portfolio,
            commission=self.config.commission,
            slippage=self.config.slippage,
            fill_on_next_open=self.config.fill_on_next_open,
            participation_rate=self.config.participation_rate,
            enforce_margin=self.config.enforce_margin,
        )
        self.trade_log = TradeLog()
        self.risk = RiskEngine(self.config.risk or RiskLimits())
        self.instruments: dict[str, Instrument] = {}
        self.frames: dict[str, pd.DataFrame] = {}
        self._exposures: list[tuple[datetime, float]] = []
        self._killed = False
        self._kill_reason: str | None = None
        self._current_ts: datetime | None = None

    # ------------------------------------------------------------------
    def _build_frames(self, snapshots: list[MarketSnapshot]) -> None:
        """One aligned DataFrame per symbol over the shared time grid."""
        n_snaps = len(snapshots)
        index = pd.DatetimeIndex([s.ts for s in snapshots])
        nan_val = float("nan")

        for symbol in self.instruments:
            # Pre-allocate column arrays for fast DataFrame construction
            opens = [nan_val] * n_snaps
            highs = [nan_val] * n_snaps
            lows = [nan_val] * n_snaps
            closes = [nan_val] * n_snaps
            volumes = [nan_val] * n_snaps

            for i, snap in enumerate(snapshots):
                bar = snap.bars.get(symbol)
                if bar is not None:
                    opens[i] = bar.open
                    highs[i] = bar.high
                    lows[i] = bar.low
                    closes[i] = bar.close
                    volumes[i] = bar.volume

            self.frames[symbol] = pd.DataFrame(
                {
                    "open": opens,
                    "high": highs,
                    "low": lows,
                    "close": closes,
                    "volume": volumes,
                },
                index=index,
            )

            if self.config.square_off_eod:
                self.frames[symbol]["ts"] = index

    # ------------------------------------------------------------------
    def run(self) -> BacktestResult:
        from forward.core.session import validate_session
        from forward.market_calendar import get_market_calendar

        if self.config.square_off_eod:
            calendar = get_market_calendar()
            validate_session(
                self.config.entry_cutoff,
                self.config.square_off_time,
                market_open=calendar.market_open,
                market_close=calendar.market_close,
            )
        snapshots = self.feed.load()
        if not snapshots:
            raise ValueError("feed produced no data")
        self.instruments = self.feed.instruments
        self._build_frames(snapshots)

        self.strategy.prepare(self.frames)

        ctx = StrategyContext(
            instruments=self.instruments,
            frames=self.frames,
            submit=self._submit,
            portfolio_getter=lambda: self.portfolio,
            cancel=self.broker.cancel,
            contract_catalog=getattr(self.feed, "contract_catalog", None),
        )
        self.risk.reset_daily(self.portfolio.equity)
        self.strategy.on_start(ctx)

        current_day: datetime.date | None = None
        for i, snap in enumerate(snapshots):
            self._current_ts = snap.ts
            # --- session rollover -------------------------------------
            if current_day is not None and snap.ts.date() != current_day:
                self.strategy.on_session_end(ctx)
                if self.config.square_off_eod and any(not p.is_flat for p in self.portfolio.positions.values()):
                    raise ValueError("Missing square-off data: a position would carry overnight")
                # Seed the daily-loss baseline from the previous close, before
                # this bar is marked. Without it the baseline would be set after
                # the mark and a daily limit could never fire on daily bars.
                self.risk.reset_daily(self.portfolio.equity)
            current_day = snap.ts.date()

            # Time-based orders are scheduled before this bar opens, not from
            # its OHLC. Ordinary strategy signals still fill on the next bar.
            if self.config.square_off_eod:
                from forward.core.session import clock, local_time
                session_time = local_time(snap.ts)
                if session_time >= clock(self.config.square_off_time):
                    self.broker.cancel_all()
                    ctx.close_all()
                elif session_time >= clock(self.config.entry_cutoff):
                    for order in list(self.broker.submitted):
                        if order.is_active and not self._reduces_position(order):
                            self.broker.cancel(order, "entry cutoff")

            # --- fills --------------------------------------------------
            for fill in self.broker.on_bar(snap):
                self.trade_log.add(fill)

            # --- mark ---------------------------------------------------
            prices = {s: b.close for s, b in snap.bars.items()}
            self.portfolio.mark(snap.ts, prices)
            self._exposures.append(
                (snap.ts, self.portfolio.gross_exposure / max(self.portfolio.equity, 1e-9))
            )

            # --- risk ---------------------------------------------------
            if not self._killed:
                verdict = self.risk.check(self.portfolio, snap.ts)
                if not verdict.allowed:
                    logger.warning("risk halt: {}", verdict.reason)
                    self._killed = True
                    self._kill_reason = verdict.reason
                    # Cancel first, close second. `close_all` only flattens
                    # held positions with fresh market orders — a resting
                    # limit/stop order (an episodic-pivot-style entry, a stop
                    # placed as its own order rather than through the sizing
                    # wrapper's protective exits) is untouched by it and
                    # would sit in the book indefinitely otherwise. Doing it
                    # the other way round would cancel the close-all orders
                    # themselves, since the broker's cancel-all has no way to
                    # tell "was resting before this bar" from "submitted this
                    # bar" — it clears the queued-market bucket too.
                    self.broker.cancel_all()
                    ctx.close_all()

            # --- strategy ------------------------------------------------
            ctx.now = snap.ts
            ctx.index = i
            ctx.bars = snap.bars
            for symbol, bar in snap.bars.items():
                ctx.windows[symbol].push(bar)
            if self._killed or i < self.config.warmup_bars:
                continue
            if not self.config.square_off_eod or session_time < clock(self.config.square_off_time):
                self.strategy.on_bar(ctx)

            if self.config.assert_invariants:
                self.portfolio.check_invariant()

        if self.config.square_off_eod and any(not p.is_flat for p in self.portfolio.positions.values()):
            raise ValueError("Missing or insufficient square-off bars: positions remain open")
        self.strategy.on_stop(ctx)
        return self._result()

    # ------------------------------------------------------------------
    def _reduces_position(self, order: Order) -> bool:
        from forward.core.enums import Side
        quantity = self.portfolio.position(order.instrument.symbol).quantity
        delta = order.remaining_quantity * (1 if order.side is Side.BUY else -1)
        return quantity * delta < 0 and abs(delta) <= abs(quantity)

    def _submit(self, order: Order) -> Order:
        if self.config.square_off_eod and self._current_ts is not None:
            from forward.core.session import clock, local_time
            if local_time(self._current_ts) >= clock(self.config.entry_cutoff) and not self._reduces_position(order):
                order.mark(OrderStatus.REJECTED, reason="entry cutoff")
                return order
        verdict = self.risk.check_order(order, self.portfolio)
        if not verdict.allowed:
            order.mark(OrderStatus.REJECTED, reason=verdict.reason)
            return order
        return self.broker.submit(order, now=self._current_ts)

    # ------------------------------------------------------------------
    def _result(self) -> BacktestResult:
        equity = self.portfolio.equity_curve()
        exposure = pd.Series(
            [e for _, e in self._exposures],
            index=pd.DatetimeIndex([t for t, _ in self._exposures]),
            name="exposure",
        )
        # Warmup supplies indicators only; its flat returns are not evidence.
        equity = equity.iloc[self.config.warmup_bars:]
        exposure = exposure.reindex(equity.index)
        metrics = compute_metrics(
            equity,
            self.trade_log.frame(),
            risk_free_rate=self.config.risk_free_rate,
            total_commission=self.portfolio.commission_paid,
            total_slippage=self.portfolio.slippage_cost,
            exposure=exposure,
            final_positions=sum(1 for p in self.portfolio.positions.values() if not p.is_flat),
        )
        return BacktestResult(
            metrics=metrics,
            equity=equity,
            cash=self.portfolio.cash_curve().reindex(equity.index),
            exposure=exposure,
            trades=self.trade_log.frame(),
            fills=_fills_frame(self.broker.fills),
            orders=_orders_frame(self.broker.submitted),
            strategy_name=self.strategy.name or type(self.strategy).__name__,
            killed=self._killed,
            kill_reason=self._kill_reason,
        )


def _fills_frame(fills: list) -> pd.DataFrame:
    if not fills:
        return pd.DataFrame(
            columns=["ts", "symbol", "side", "quantity", "price", "commission", "slippage"]
        )
    return pd.DataFrame(
        [
            {
                "ts": f.ts,
                "symbol": f.instrument.symbol,
                "side": f.side.name,
                "quantity": f.quantity,
                "price": f.price,
                "commission": f.commission,
                "slippage": f.slippage,
            }
            for f in fills
        ]
    )


def _orders_frame(orders: list[Order]) -> pd.DataFrame:
    if not orders:
        return pd.DataFrame(
            columns=["order_id", "symbol", "side", "quantity", "type", "status", "filled"]
        )
    return pd.DataFrame(
        [
            {
                "order_id": o.order_id,
                "symbol": o.instrument.symbol,
                "side": o.side.name,
                "quantity": o.quantity,
                "type": o.order_type.value,
                "status": o.status.value,
                "filled": o.filled_quantity,
                "avg_fill_price": o.avg_fill_price,
                "reject_reason": o.reject_reason,
            }
            for o in orders
        ]
    )
