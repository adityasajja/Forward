"""Intelligent Buy & Sell Signal Engine and Automated Periodic Monitor.

Continuously evaluates watched stocks and portfolio holdings against quantitative
trend, momentum, and risk indicators (SMA crossovers, RSI thresholds, trailing stops,
take-profit, and volume breakouts) and dispatches instant actionable Telegram alerts.
"""

from __future__ import annotations

import asyncio
import math
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Literal

import pandas as pd
from loguru import logger
from pydantic import BaseModel, Field

from forward.alerts.models import AlertEvent
from forward.core.alert_config import IntelligentAlertConfig
from forward.core.alert_ports import (
    AlertDeliveryPort,
    AlertHoldingsPort,
    AlertStorePort,
    IntelligentAlertConfigPort,
)
from forward.core.historical_market_data_ports import CachedMarketFramesPort
from forward.core.learning_ports import (
    SetupStatisticsPort,
)
from forward.core.session import is_market_session_open
from forward.core.trade_signal_models import TradeSignal
from forward.core.trade_signal_ports import (
    TradeSignalQueuePort,
    TradeSignalSettingsPort,
)
from forward.strategy.indicators import crossover, rsi, sma


class IntelligentSignal(BaseModel):
    symbol: str
    action: Literal["BUY", "SELL"]
    reason: str
    price: float
    day_chg_pct: float
    rsi: float | None = None
    metric: str = ""
    ts: datetime = Field(default_factory=datetime.now)


def evaluate_stock_signals(
    symbol: str,
    df: pd.DataFrame,
    cfg: IntelligentAlertConfig,
    holding_info: dict[str, Any] | None = None,
) -> list[IntelligentSignal]:
    """Evaluates a single stock against buy and sell quantitative rules."""
    signals: list[IntelligentSignal] = []
    if df is None:
        raise ValueError(f"No market frame is available for {symbol}.")
    if len(df) < 30:
        raise ValueError(
            f"Market frame for {symbol} has {len(df)} rows; at least 30 are required."
        )

    df = df.sort_values("ts").reset_index(drop=True)
    c = df["close"]
    last = float(c.iloc[-1])
    prev = float(c.iloc[-2]) if len(c) > 1 else last
    day_chg = round(((last / prev) - 1) * 100, 2)

    s20 = sma(c, 20)
    s50 = sma(c, 50)
    rsi_vals = rsi(c)
    if rsi_vals.empty or not math.isfinite(float(rsi_vals.iloc[-1])):
        raise ValueError(f"A valid RSI value is required for {symbol}.")
    last_rsi = round(float(rsi_vals.iloc[-1]), 1)

    last_s20 = float(s20.iloc[-1]) if not s20.empty else last
    prev_s20 = float(s20.iloc[-2]) if len(s20) > 1 else last_s20

    # -----------------
    # 1. SELL SIGNALS — one intelligent exit call per symbol
    # -----------------
    # Hard exits (breached risk limit or hit target) are deterministic P&L
    # facts and bypass scoring entirely — they fire alone, at max confidence.
    # Everything else (trend breakdown, RSI overbought, trailing stop) is a
    # "soft" warning sign that only means something in confluence, so those
    # are weighted and combined into a single composite call instead of
    # stacking separate alerts for what is really one deteriorating position.
    hard_exit: IntelligentSignal | None = None
    if holding_info:
        avg_price = float(holding_info.get("avg_price") or holding_info.get("buy_price") or 0)
        if avg_price > 0:
            pnl_pct = round(((last / avg_price) - 1) * 100, 2)
            if cfg.sell_take_profit_enabled and pnl_pct >= cfg.sell_take_profit_pct:
                hard_exit = IntelligentSignal(
                    symbol=symbol,
                    action="SELL",
                    reason=f"Target profit hit: Position at {pnl_pct:+.1f}% P&L (Target {cfg.sell_take_profit_pct}%). Lock gains!",
                    price=last,
                    day_chg_pct=day_chg,
                    rsi=last_rsi,
                    metric=f"Target +{cfg.sell_take_profit_pct}% Reached",
                )
            elif cfg.sell_stop_loss_enabled and pnl_pct <= -abs(cfg.sell_stop_loss_pct):
                hard_exit = IntelligentSignal(
                    symbol=symbol,
                    action="SELL",
                    reason=f"Max loss cut: Position down {pnl_pct:.1f}% P&L (Limit -{cfg.sell_stop_loss_pct}%). Protect capital.",
                    price=last,
                    day_chg_pct=day_chg,
                    rsi=last_rsi,
                    metric=f"Stop Loss -{cfg.sell_stop_loss_pct}%",
                )

    if hard_exit is not None:
        signals.append(hard_exit)
    else:
        score = 0.0
        reasons: list[str] = []
        metrics: list[str] = []

        # Trend breakdown: price drops below SMA20 when previously above
        if cfg.sell_sma_breakdown and last < last_s20 and prev >= prev_s20:
            score += 1.0
            metrics.append("SMA20 Breakdown")
            reasons.append(
                f"trend broke down (price ₹{last:,.2f} crossed below 20-SMA ₹{last_s20:,.2f})"
            )

        # RSI overbought reversal risk
        if cfg.sell_rsi_overbought and last_rsi >= cfg.sell_rsi_threshold:
            score += 1.0
            metrics.append("RSI Overbought")
            reasons.append(f"momentum overbought (RSI {last_rsi:.1f} ≥ {cfg.sell_rsi_threshold})")

        # Trailing stop from recent 20-day peak
        recent_high = float(df["high"].tail(20).max())
        drop_from_high = ((last / recent_high) - 1) * 100
        if cfg.sell_trailing_stop_enabled and drop_from_high <= -abs(cfg.sell_trailing_stop_pct):
            score += 1.5
            metrics.append(f"Trailing Drop > {cfg.sell_trailing_stop_pct}%")
            reasons.append(f"down {drop_from_high:.1f}% from its 20-day high (₹{recent_high:,.2f})")

        if score >= cfg.sell_composite_min_score:
            signals.append(
                IntelligentSignal(
                    symbol=symbol,
                    action="SELL",
                    reason=(
                        f"Composite exit (confluence score {score:.1f}/{cfg.sell_composite_min_score:.1f}): "
                        + "; ".join(reasons)
                        + "."
                    ),
                    price=last,
                    day_chg_pct=day_chg,
                    rsi=last_rsi,
                    metric="Composite Exit: " + ", ".join(metrics),
                )
            )

    # -----------------
    # 2. BUY SIGNALS
    # -----------------
    # A. Golden Cross: SMA20 crosses above SMA50 within the last 3 bars
    if cfg.buy_golden_cross and bool(crossover(s20, s50).tail(3).any()):
        signals.append(
            IntelligentSignal(
                symbol=symbol,
                action="BUY",
                reason=f"Golden Cross: 20-SMA crossed above 50-SMA near ₹{last:,.2f} with RSI {last_rsi:.0f}.",
                price=last,
                day_chg_pct=day_chg,
                rsi=last_rsi,
                metric="Golden Cross (SMA 20/50)",
            )
        )

    # B. Oversold Bounce
    if cfg.buy_rsi_oversold and last_rsi <= cfg.buy_rsi_threshold:
        signals.append(
            IntelligentSignal(
                symbol=symbol,
                action="BUY",
                reason=f"Oversold territory: RSI at {last_rsi:.1f} (≤ {cfg.buy_rsi_threshold}). Potential mean-reversion opportunity.",
                price=last,
                day_chg_pct=day_chg,
                rsi=last_rsi,
                metric="RSI Oversold Dip",
            )
        )

    # C. Breakout with Volume Surge
    if cfg.buy_breakout_vol:
        if "volume" not in df.columns:
            raise ValueError(f"Volume data is required for breakout checks on {symbol}.")
        if len(df) < 21:
            raise ValueError(
                f"At least 21 rows are required for volume breakout checks on {symbol}."
            )
        recent_20_high = float(df["high"].iloc[-21:-1].max())
        vol_avg = float(df["volume"].tail(20).mean())
        cur_vol = float(df["volume"].iloc[-1])
        if not math.isfinite(vol_avg) or not math.isfinite(cur_vol) or vol_avg <= 0:
            raise ValueError(f"Valid current and average volume are required for {symbol}.")
        vol_x = cur_vol / vol_avg
        if last > recent_20_high and vol_x >= 1.5:
            signals.append(
                IntelligentSignal(
                    symbol=symbol,
                    action="BUY",
                    reason=f"Volume Breakout: Price broke 20-day high (₹{recent_20_high:,.2f}) on {vol_x:.1f}x average volume.",
                    price=last,
                    day_chg_pct=day_chg,
                    rsi=last_rsi,
                    metric="Breakout + Vol Spike",
                )
            )

    # D. Box Oscillation: price is sitting near the floor of a tight,
    # sideways range — a mean-reversion bounce rather than a trend trade.
    if cfg.buy_box_oscillation and len(df) >= cfg.buy_box_lookback:
        window = df.tail(cfg.buy_box_lookback)
        floor = float(window["low"].min())
        ceiling = float(window["high"].max())
        if floor > 0:
            box_width_pct = ((ceiling / floor) - 1) * 100
            dist_from_floor_pct = ((last / floor) - 1) * 100
            if (
                box_width_pct <= cfg.buy_box_width_pct
                and 0 <= dist_from_floor_pct <= cfg.buy_box_floor_pct
            ):
                signals.append(
                    IntelligentSignal(
                        symbol=symbol,
                        action="BUY",
                        reason=(
                            f"Box oscillation: range-bound {box_width_pct:.1f}% over "
                            f"{cfg.buy_box_lookback} bars (₹{floor:,.2f}–₹{ceiling:,.2f}), "
                            f"now {dist_from_floor_pct:.1f}% above the floor."
                        ),
                        price=last,
                        day_chg_pct=day_chg,
                        rsi=last_rsi,
                        metric="Box Floor Bounce",
                    )
                )

    return signals


class IntelligentMonitorManager:
    """Coordinate periodic alerts using injected storage and market ports."""

    def __init__(
        self,
        *,
        store: AlertStorePort,
        market_frames: CachedMarketFramesPort,
        holdings: AlertHoldingsPort,
        channels: list[AlertDeliveryPort],
        config: IntelligentAlertConfigPort,
        setup_stats: SetupStatisticsPort,
        signal_queue: TradeSignalQueuePort,
        signal_settings: TradeSignalSettingsPort,
        trade_signal_factory: Callable[..., TradeSignal],
    ) -> None:
        """Create the monitor with its required application ports.

        Args:
            store: Alert rules and event storage.
            market_frames: Daily market history reader.
            holdings: Current account holdings reader.
            channels: Configured alert delivery channels.
            config: Storage for intelligent alert settings.
            setup_stats: Historical result lookup for signal summaries.
            signal_queue: Queue for actionable signal cards.
            signal_settings: Active sizing and expiry settings.
            trade_signal_factory: Application service that builds a trade signal.
        """
        self._store = store
        self._market_frames = market_frames
        self._holdings = holdings
        self._channels = channels
        self._config = config
        self._setup_stats = setup_stats
        self._signal_queue = signal_queue
        self._signal_settings = signal_settings
        self._trade_signal_factory = trade_signal_factory
        self._running = False
        self._cycle_lock = threading.Lock()
        self._task: asyncio.Task | None = None
        self._last_run: datetime | None = None
        self._recent_signals: list[dict[str, Any]] = []
        self._cooldown_tracker: dict[
            str, datetime
        ] = {}  # "SYMBOL:ACTION:METRIC" -> last_alerted_at

    def get_status(self) -> dict[str, Any]:
        cfg = self._config.load()
        return {
            "enabled": cfg.enabled,
            "running": self._running,
            "last_run": self._last_run.isoformat() if self._last_run else None,
            "market_open": is_market_session_open(),
            "interval_min": cfg.interval_min,
            "universe": cfg.universe,
            "recent_signals": self._recent_signals[-30:],
        }

    def get_config(self) -> IntelligentAlertConfig:
        """Return the saved intelligent alert settings."""
        return self._config.load()

    def update_config(self, values: dict[str, Any]) -> IntelligentAlertConfig:
        """Update selected settings and persist the complete configuration.

        Args:
            values: Settings supplied by the API caller.

        Returns:
            The validated, saved settings.
        """
        updated = self._config.load().model_dump()
        updated.update(values)
        return self._config.save(IntelligentAlertConfig(**updated))

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Started Intelligent Alerts Periodic Monitor background task.")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        logger.info("Stopped Intelligent Alerts Periodic Monitor background task.")

    async def run_evaluation_cycle(self, force: bool = False) -> list[IntelligentSignal]:
        """Runs an evaluation cycle across target stocks and fires Telegram alerts.

        The cycle is blocking work: it reads every cached frame, evaluates rules, and
        posts each alert to Telegram over HTTP. Run on the event loop, one unreachable
        Telegram host (a 10s connect timeout per alert) froze the whole server — no page
        loaded and startup never completed. It runs on a worker thread instead.
        """
        return await asyncio.to_thread(self._run_evaluation_cycle_blocking, force)

    def _run_evaluation_cycle_blocking(self, force: bool = False) -> list[IntelligentSignal]:
        with self._cycle_lock:  # an API-triggered cycle and the timer must not overlap
            return self._evaluate_cycle(force)

    def _evaluate_cycle(self, force: bool = False) -> list[IntelligentSignal]:
        cfg = self._config.load()
        if not cfg.enabled and not force:
            return []

        store = self._store
        channels = self._channels

        # Collect symbols to evaluate
        symbols_to_check: set[str] = set()
        holdings_dict: dict[str, dict[str, Any]] = {}

        # 1. Fetch holdings when configured.
        if cfg.universe in ("holdings", "both"):
            for sym, info in self._holdings.held_symbols().items():
                symbols_to_check.add(sym)
                holdings_dict[sym] = {
                    "avg_price": float(info.get("avg_price") or 0),
                    "qty": float(info.get("qty") or 0),
                }

        # 2. Add Watchlist symbols if needed
        if cfg.universe in ("watchlist", "both"):
            # Include the selected cached universe and custom rule symbols.
            for r in store.rules():
                symbols_to_check.add(r.symbol)
            symbols_to_check.update(self._market_frames.symbols("NSEEQ"))

        if not symbols_to_check:
            raise ValueError("The configured alert universe contains no symbols.")

        # Load daily frames from cache
        all_frames = self._market_frames.frames("NSEEQ")
        generated_signals: list[IntelligentSignal] = []
        now = datetime.now()

        for sym in symbols_to_check:
            df = all_frames.get(sym)
            if df is None:
                raise ValueError(f"No cached market frame is available for {sym}.")

            signals = evaluate_stock_signals(sym, df, cfg, holdings_dict.get(sym))
            for sig in signals:
                tracker_key = f"{sig.symbol}:{sig.action}:{sig.metric}"
                last_sent = self._cooldown_tracker.get(tracker_key)
                if last_sent and (now - last_sent) < timedelta(minutes=cfg.cooldown_min):
                    continue  # Cooldown active

                self._cooldown_tracker[tracker_key] = now
                generated_signals.append(sig)

                # Format Telegram Notification
                icon = "🟢 [BUY SIGNAL]" if sig.action == "BUY" else "🔴 [SELL SIGNAL]"
                header = f"Forward Intelligent Trigger: {icon} {sig.symbol.replace('-EQ', '')}"
                detail = (
                    f"{sig.reason}\n"
                    f"• Price: ₹{sig.price:,.2f} ({sig.day_chg_pct:+.2f}%)\n"
                    f"• RSI: {sig.rsi if sig.rsi else '—'}\n"
                    f"• Strategy Metric: {sig.metric}\n"
                    f"• Time: {now.strftime('%I:%M %p IST')}"
                )

                # Send down channels
                sent_on = "none"
                delivered = False
                for ch in channels:
                    try:
                        if ch.send(header, detail):
                            sent_on = ch.name
                            delivered = True
                            break
                    except Exception as err:
                        logger.warning("Channel send failed: {}", err)

                # Log event in AlertStore
                evt = AlertEvent(
                    rule_id=f"intel-{sig.action.lower()}",
                    rule=f"{icon} {sig.symbol.replace('-EQ', '')} · {sig.metric}",
                    message=detail,
                    channel=sent_on,
                    ok=delivered,
                )
                store.log(evt)
                self._recent_signals.append(
                    {
                        "symbol": sig.symbol,
                        "action": sig.action,
                        "reason": sig.reason,
                        "price": sig.price,
                        "day_chg_pct": sig.day_chg_pct,
                        "rsi": sig.rsi,
                        "metric": sig.metric,
                        "ts": now.isoformat(),
                        "channel": sent_on,
                        "delivered": delivered,
                    }
                )

                # Also queue as a semi-automatic trade signal if it's actionable.
                #
                # A SELL only means something if there is something to sell. The
                # rules above run over the whole `symbols_to_check` set — holdings
                # plus the selected cached universe when
                # `cfg.universe` includes "watchlist" — so "trend broke down" /
                # "RSI overbought" / "trailing stop" fire for a symbol never
                # bought just as readily as for one actually held. Queuing that
                # as an executable trade signal put a real, approvable SELL
                # order (with a fabricated stop/target/quantity, as if opening a
                # fresh short) in front of the user for stock sitting in nobody's
                # portfolio. The Telegram/log alert above stays either way — a
                # "this broke down" notice is still useful watchlist intel — only
                # the *actionable, executable* queue entry is gated on holding it.
                if sig.action == "SELL" and sym not in holdings_dict:
                    continue
                ts_settings = self._signal_settings.load()
                tq = self._signal_queue
                if tq.active_count() < ts_settings.max_active:
                    already_pending = [
                        s
                        for s in tq.pending()
                        if s.symbol == sig.symbol and s.action == sig.action
                    ]
                    if not already_pending:
                        trade_sig = self._trade_signal_factory(
                            symbol=sig.symbol,
                            action=sig.action,
                            setup=sig.metric,
                            reason=sig.reason,
                            entry_price=sig.price,
                            df=df,
                            settings=ts_settings,
                            rsi_val=sig.rsi,
                            setup_stats=self._setup_stats,
                        )
                        tq.add(trade_sig)

        # 3. Capital rotation: a held winner that has gone quiet, paired with
        # whatever is showing real momentum right now. Separate from the
        # per-symbol loop above because naming a replacement requires looking
        # across the *whole* scanned universe, not just the one symbol being
        # evaluated — see `forward.capital_rotation`.
        if cfg.sell_stall_enabled and holdings_dict:
            try:
                from forward.capital_rotation import best_momentum_candidate, read_stall
                ts_settings = self._signal_settings.load()
                tq = self._signal_queue
                for sym, info in holdings_dict.items():
                    df = all_frames.get(sym)
                    if df is None:
                        raise ValueError(f"No cached market frame is available for {sym}.")
                    avg_price = float(info.get("avg_price") or 0)
                    stall = read_stall(
                        df,
                        avg_price,
                        window_days=cfg.sell_stall_days,
                        tight_atr_mult=cfg.sell_stall_atr_mult,
                        min_gain_pct=cfg.sell_stall_min_gain_pct,
                    )
                    if stall is None:
                        continue

                    tracker_key = f"{sym}:ROTATE"
                    last_sent = self._cooldown_tracker.get(tracker_key)
                    if last_sent and (now - last_sent) < timedelta(minutes=cfg.cooldown_min):
                        continue

                    candidate = best_momentum_candidate(
                        all_frames, exclude=set(holdings_dict) | {sym}
                    )
                    if candidate is None:
                        continue  # nothing better to rotate into right now

                    self._cooldown_tracker[tracker_key] = now
                    reason_a = (
                        f"Stalled {stall.days_stalled} sessions (range only {stall.range_over_atr:.1f}x "
                        f"ATR) since peaking at ₹{stall.high_in_window:,.2f} — up {stall.gain_from_avg_pct:+.1f}% "
                        f"from your average, but not moving. {candidate.symbol.replace('-EQ', '')} is showing "
                        f"real momentum now ({candidate.roc_pct:+.1f}% in 20d, RSI {candidate.rsi:.0f}) — "
                        f"consider rotating this capital there."
                    )
                    header = f"Forward Intelligent Trigger: 🔄 [ROTATE] {sym.replace('-EQ', '')} → {candidate.symbol.replace('-EQ', '')}"
                    sent_on = "none"
                    delivered = False
                    for ch in channels:
                        try:
                            if ch.send(header, reason_a):
                                sent_on = ch.name
                                delivered = True
                                break
                        except Exception as err:
                            logger.warning("Channel send failed: {}", err)
                    store.log(
                        AlertEvent(
                            rule_id="intel-rotate",
                            rule=f"🔄 [ROTATE] {sym.replace('-EQ', '')} → {candidate.symbol.replace('-EQ', '')}",
                            message=reason_a,
                            channel=sent_on,
                            ok=delivered,
                        )
                    )

                    if tq.active_count() + 1 >= ts_settings.max_active:
                        continue
                    already = [
                        s for s in tq.pending() if s.symbol == sym and s.setup == "Capital Rotation"
                    ]
                    if already:
                        continue

                    sell_sig = self._trade_signal_factory(
                        symbol=sym,
                        action="SELL",
                        setup="Capital Rotation",
                        reason=reason_a,
                        entry_price=stall.last_price,
                        df=df,
                        settings=ts_settings,
                        setup_stats=self._setup_stats,
                    )
                    # A rotation liquidates the actual position — sizing it off
                    # `_compute_quantity`'s risk-based guess (right for opening
                    # a *new* trade) would sell an arbitrary amount instead of
                    # what is actually held.
                    held_qty = info.get("qty")
                    if held_qty:
                        sell_sig.quantity = int(held_qty)
                        sell_sig.risk_amount = round(
                            sell_sig.quantity * abs(sell_sig.entry_price - sell_sig.stop_loss), 2
                        )
                    tq.add(sell_sig)

                    buy_reason = (
                        f"Rotation candidate for {sym.replace('-EQ', '')}: "
                        f"{candidate.roc_pct:+.1f}% over 20 sessions, RSI {candidate.rsi:.0f}, "
                        f"trading above both its 20- and 50-day averages."
                    )
                    buy_sig = self._trade_signal_factory(
                        symbol=candidate.symbol,
                        action="BUY",
                        setup="Rotation Candidate",
                        reason=buy_reason,
                        entry_price=candidate.price,
                        df=all_frames[candidate.symbol],
                        settings=ts_settings,
                        rsi_val=candidate.rsi,
                        setup_stats=self._setup_stats,
                    )
                    tq.add(buy_sig)
            except Exception as exc:
                logger.error("Capital rotation evaluation failed: {}", exc)
                raise

        self._last_run = now
        return generated_signals

    async def _loop(self) -> None:
        while self._running:
            try:
                cfg = self._config.load()
                if cfg.enabled and is_market_session_open():
                    logger.debug("Executing scheduled intelligent alert evaluation cycle...")
                    await self.run_evaluation_cycle(force=False)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in intelligent alert loop: {}", e)

            # Wait for user configured interval
            cfg = self._config.load()
            interval_sec = max(60, cfg.interval_min * 60)
            await asyncio.sleep(interval_sec)
