"""Build trade signals from market data and configured risk rules."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Literal

import numpy as np
import pandas as pd

from forward.core.learning_ports import SetupStatisticsPort
from forward.core.session import IST
from forward.core.trade_signal_models import TradeSignal, TradeSignalSettings
from forward.strategy.indicators import atr


def compute_stop_and_target(
    df: pd.DataFrame,
    entry: float,
    action: Literal["BUY", "SELL"],
    settings: TradeSignalSettings,
) -> tuple[float, float]:
    """Calculate the stop and target prices for a signal.

    Args:
        df: Price bars used to calculate the ATR when selected.
        entry: Reference entry price.
        action: Direction of the proposed trade.
        settings: Risk and target rules for this signal.

    Returns:
        The stop price followed by the target price.

    Raises:
        ValueError: If the entry, selected stop input, or calculated prices are
            missing or invalid.
    """
    if not math.isfinite(entry) or entry <= 0:
        raise ValueError(f"entry price must be finite and positive, got {entry!r}")
    if not math.isfinite(settings.rr_ratio) or settings.rr_ratio <= 0:
        raise ValueError("reward-to-risk ratio must be finite and positive")

    if settings.stop_method == "atr":
        required = {"high", "low", "close"}
        missing = sorted(required - set(df.columns))
        if missing:
            raise ValueError(f"ATR stop needs these bar columns: {', '.join(missing)}")
        if len(df) < 20:
            raise ValueError(f"ATR stop needs at least 20 bars; got {len(df)}")
        recent_bars = df.loc[:, ["high", "low", "close"]].tail(20).to_numpy(dtype=float)
        if not np.isfinite(recent_bars).all():
            raise ValueError("ATR stop needs 20 bars with finite high, low, and close values")
        highs, lows, closes = recent_bars.T
        if (lows <= 0).any() or (closes <= 0).any() or (highs < lows).any():
            raise ValueError("ATR stop needs positive prices with each high at or above its low")
        if not math.isfinite(settings.stop_atr_mult) or settings.stop_atr_mult <= 0:
            raise ValueError("ATR stop multiplier must be finite and positive")
        atr_value = float(atr(df["high"], df["low"], df["close"]).iloc[-1])
        risk_points = settings.stop_atr_mult * atr_value
        if not math.isfinite(atr_value) or atr_value <= 0:
            raise ValueError(f"ATR stop needs a finite positive ATR value, got {atr_value!r}")
    elif settings.stop_method == "pct":
        if not math.isfinite(settings.stop_pct) or settings.stop_pct <= 0:
            raise ValueError("percentage stop must be finite and positive")
        risk_points = entry * settings.stop_pct / 100.0
    else:
        raise ValueError(f"unsupported stop method: {settings.stop_method!r}")

    if not math.isfinite(risk_points) or risk_points <= 0:
        raise ValueError(f"stop distance must be finite and positive, got {risk_points!r}")

    if action == "BUY":
        stop = round(entry - risk_points, 2)
        target = round(entry + risk_points * settings.rr_ratio, 2)
    elif action == "SELL":
        stop = round(entry + risk_points, 2)
        target = round(entry - risk_points * settings.rr_ratio, 2)
    else:
        raise ValueError(f"unsupported trade action: {action!r}")
    if stop <= 0 or target <= 0 or stop == entry:
        raise ValueError("calculated stop and target must be positive with nonzero risk")
    return stop, target


def compute_quantity(
    entry: float,
    stop_loss: float,
    settings: TradeSignalSettings,
) -> int:
    """Calculate the signal quantity from its risk budget.

    Args:
        entry: Reference entry price.
        stop_loss: Stop price used to measure per-share risk.
        settings: Capital and risk percentage for this signal.

    Returns:
        The number of shares to trade.

    Raises:
        ValueError: If the risk inputs are invalid or the budget cannot cover one
            share at the calculated stop distance.
    """
    if not math.isfinite(entry) or entry <= 0:
        raise ValueError(f"entry price must be finite and positive, got {entry!r}")
    if not math.isfinite(stop_loss) or stop_loss <= 0:
        raise ValueError(f"stop price must be finite and positive, got {stop_loss!r}")
    risk_per_share = abs(entry - stop_loss)
    if risk_per_share <= 0:
        raise ValueError("stop distance must be positive")
    if not math.isfinite(settings.capital) or settings.capital <= 0:
        raise ValueError("capital must be finite and positive")
    if (
        not math.isfinite(settings.risk_per_trade_pct)
        or settings.risk_per_trade_pct <= 0
    ):
        raise ValueError("risk per trade must be finite and positive")
    max_loss = settings.capital * settings.risk_per_trade_pct / 100.0
    if not math.isfinite(max_loss) or max_loss <= 0:
        raise ValueError("risk budget must be finite and positive")
    quantity = int(max_loss / risk_per_share)
    if quantity < 1:
        raise ValueError(
            "risk budget cannot cover one share at the calculated stop distance"
        )
    return quantity


def intraday_expiry(now: datetime) -> datetime:
    """Return the next 14:30 IST signal expiry at or after ``now``.

    Args:
        now: Current timezone-aware time.

    Returns:
        Today's 14:30 expiry or tomorrow's expiry when today's has passed.
    """
    cutoff = now.replace(hour=14, minute=30, second=0, microsecond=0)
    if cutoff <= now:
        cutoff += timedelta(days=1)
    return cutoff


def build_trade_signal(
    symbol: str,
    action: Literal["BUY", "SELL"],
    setup: str,
    reason: str,
    entry_price: float,
    df: pd.DataFrame,
    settings: TradeSignalSettings,
    setup_stats: SetupStatisticsPort,
    rsi_val: float | None = None,
    vol_x: float | None = None,
    paper_citation: str | None = None,
    thesis: str | None = None,
    confidence_score: float | None = None,
    expected_value: float | None = None,
    regime_fit: str | None = None,
) -> TradeSignal:
    """Build a signal with risk levels and saved setup statistics.

    Args:
        symbol: Market symbol for the proposed trade.
        action: Direction of the proposed trade.
        setup: Rule or research setup that created the signal.
        reason: Short explanation for the proposed trade.
        entry_price: Reference entry price.
        df: Price bars used to calculate risk levels.
        settings: Risk and expiry rules for this signal.
        setup_stats: Historical statistics for the named setup.
        rsi_val: RSI value when the signal was created.
        vol_x: Volume multiple when the signal was created.
        paper_citation: Research source for the signal.
        thesis: Research explanation for the signal.
        confidence_score: Confidence assigned by the research system.
        expected_value: Expected value assigned by the research system.
        regime_fit: Market regime used to qualify the research signal.

    Returns:
        A new pending trade signal.
    """
    stop, target = compute_stop_and_target(df, entry_price, action, settings)
    quantity = compute_quantity(entry_price, stop, settings)
    risk_amount = round(quantity * abs(entry_price - stop), 2)
    reward_to_risk = round(abs(target - entry_price) / max(abs(entry_price - stop), 0.01), 2)
    setup_stat = setup_stats.for_setup(setup)
    expires_at = intraday_expiry(datetime.now(tz=IST))

    return TradeSignal(
        symbol=symbol,
        action=action,
        setup=setup,
        reason=reason,
        entry_price=entry_price,
        stop_loss=stop,
        target=target,
        quantity=quantity,
        risk_amount=risk_amount,
        rr_ratio=reward_to_risk,
        rsi=rsi_val,
        vol_x=vol_x,
        setup_hit_rate_pct=setup_stat.hit_rate_pct if setup_stat else None,
        setup_median_days_to_target=(setup_stat.median_days_to_target if setup_stat else None),
        paper_citation=paper_citation,
        thesis=thesis,
        confidence_score=confidence_score,
        expected_value=expected_value,
        regime_fit=regime_fit,
        expires_at=expires_at,
    )
