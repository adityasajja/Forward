"""Regime-Adaptive Multi-Strategy Meta-Selector.

Uses Laya (`convaiinnovations/laya`) to evaluate market conditions (volatility,
trend persistence, momentum) and dynamically route capital to the optimal strategy:

- "TRENDING_BULLISH": Allocate to Momentum / Trend following (e.g. SmaCrossover, MomentumBreakout).
- "TRENDING_BEARISH": Defensive / Short exposure if permitted, else stay flat / cash.
- "MEAN_REVERTING": Allocate to mean-reversion or tight oscillating strategies.
- "HIGH_VOLATILITY": Defensive cash preservation or volatility breakout with reduced sizing.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from forward.strategy.base import Strategy, StrategyInputUnavailable
from forward.strategy.indicators import adx, atr, rsi, sma
from forward.strategy.regime_laya import get_regime_classifier
from forward.strategy.sizing import PositionSizingEngine, RoundingRule, SizingConfig, SizingMethod


class RegimeAdaptiveStrategy(Strategy):
    name = "regime_adaptive"
    available_capital_fraction: float | None = None
    rounding_rule: str | None = None

    fast: int = 20
    slow: int = 50
    adx_period: int = 14
    atr_period: int = 14
    rsi_period: int = 14
    return_period: int = 20
    allocation: float = 0.25
    eval_interval: int = 5  # Evaluate regime every N bars to avoid excessive reclassification
    oversold_rsi: float = 32.0
    overbought_rsi: float = 68.0
    oversold_allocation: float = 0.7

    def __init__(self, **params) -> None:
        super().__init__(**params)
        self.classifier = get_regime_classifier()
        self._regime_cache: dict[str, str] = {}
        self._bar_count: dict[str, int] = {}

    def prepare(self, frames: dict[str, pd.DataFrame]) -> None:
        for frame in frames.values():
            frame["sma_fast"] = sma(frame["close"], self.fast)
            frame["sma_slow"] = sma(frame["close"], self.slow)
            frame["sma_ratio"] = frame["sma_fast"] / frame["sma_slow"]
            frame["atr"] = atr(frame["high"], frame["low"], frame["close"], self.atr_period)
            frame["atr_pct"] = (frame["atr"] / frame["close"]) * 100.0
            frame["adx"] = adx(frame["high"], frame["low"], frame["close"], self.adx_period)
            frame["rsi"] = rsi(frame["close"], self.rsi_period)
            frame["return"] = frame["close"].pct_change(self.return_period) * 100.0

            # Previous bar averages for crossovers
            frame["sma_fast_prev"] = frame["sma_fast"].shift(1)
            frame["sma_slow_prev"] = frame["sma_slow"].shift(1)

    def on_bar(self, ctx) -> None:
        equity = ctx.equity
        for symbol in ctx.instruments:
            row = ctx.row(symbol)
            warmup_bars = max(
                self.fast + 1,
                self.slow + 1,
                self.atr_period,
                self.rsi_period,
                self.return_period + 1,
                self.adx_period * 2,
            )
            if ctx.index + 1 < warmup_bars:
                continue

            fast_now, slow_now = row["sma_fast"], row["sma_slow"]
            price = row["close"]

            inputs = {
                "close": price,
                "sma_fast": fast_now,
                "sma_slow": slow_now,
                "sma_fast_prev": row["sma_fast_prev"],
                "sma_slow_prev": row["sma_slow_prev"],
                "atr_pct": row["atr_pct"],
                "adx": row["adx"],
                "rsi": row["rsi"],
                "return": row["return"],
                "sma_ratio": row["sma_ratio"],
            }
            missing = [
                name
                for name, value in inputs.items()
                if pd.isna(value) or not np.isfinite(value)
            ]
            if "close" not in missing and price <= 0:
                missing.append("close")
            if missing:
                self._regime_cache.pop(symbol, None)
                raise StrategyInputUnavailable(
                    f"{self.name} cannot evaluate {symbol}: missing indicator values "
                    f"{', '.join(missing)} after {warmup_bars} warmup bars"
                )

            count = self._bar_count.get(symbol, 0)
            self._bar_count[symbol] = count + 1

            # Periodically evaluate market regime via Laya decision model
            if symbol not in self._regime_cache or (count % self.eval_interval == 0):
                verdict = self.classifier.classify_market_context(
                    symbol=symbol,
                    atr_pct=float(inputs["atr_pct"]),
                    adx=float(inputs["adx"]),
                    rsi=float(inputs["rsi"]),
                    return_20d=float(inputs["return"]),
                    sma_ratio=float(inputs["sma_ratio"]),
                )
                self._regime_cache[symbol] = verdict.regime

            current_regime = self._regime_cache.get(symbol)
            if current_regime is None:
                continue

            # Route actions based on active market regime
            fast_prev, slow_prev = inputs["sma_fast_prev"], inputs["sma_slow_prev"]

            target_qty = self._size(ctx, symbol, price, equity)
            if target_qty == 0:
                continue

            if current_regime == "TRENDING_BULLISH":
                # Favor trend entries
                if fast_now > slow_now and fast_prev <= slow_prev:
                    ctx.target(symbol, target_qty, tag="laya-bullish-trend")
            elif current_regime in ("HIGH_VOLATILITY", "TRENDING_BEARISH"):
                # Exit equity positions or protect capital
                pos = ctx.portfolio.positions.get(symbol)
                if pos and pos.quantity > 0:
                    ctx.target(symbol, 0, tag=f"laya-exit-{current_regime.lower()}")
            elif current_regime == "MEAN_REVERTING":
                # Range-bound regime: buy oversold, take profits on overbought
                rsi_value = float(inputs["rsi"])
                if rsi_value < self.oversold_rsi:
                    ctx.target(symbol, int(target_qty * self.oversold_allocation), tag="laya-oversold-reversion")
                elif rsi_value > self.overbought_rsi:
                    ctx.target(symbol, 0, tag="laya-overbought-exit")

    def _size(self, ctx, symbol: str, price: float, equity: float) -> int:
        """Size a position with the shared sizing engine and instrument limits.

        Args:
            ctx: Current strategy context.
            symbol: Instrument symbol to size.
            price: Current bar close.
            equity: Current portfolio equity.

        Returns:
            A quantity rounded down to the instrument's quantity step.
        """
        instrument = ctx.instruments[symbol]
        if self.available_capital_fraction is None:
            raise ValueError(
                "regime_adaptive requires an explicit available_capital_fraction"
            )
        if self.rounding_rule is None:
            raise ValueError("regime_adaptive requires an explicit rounding_rule")
        buying_power = ctx.buying_power
        if buying_power is None:
            raise ValueError(
                "regime_adaptive cannot size positions because margin is unknown"
            )
        config = SizingConfig(
            method=SizingMethod.PERCENT_OF_CAPITAL,
            capital_fraction=self.allocation,
            rounding_rule=RoundingRule(self.rounding_rule),
            available_capital_fraction=self.available_capital_fraction,
        )
        result = PositionSizingEngine.calculate(
            config,
            entry_price=price,
            capital=equity,
            available_capital=buying_power,
            multiplier=instrument.multiplier,
        )
        step = instrument.quantity_step
        if step <= 0:
            raise ValueError(f"{symbol} has an invalid quantity step: {step}")
        return result.final_quantity // step * step

    def describe_signal(self, symbol: str, ctx) -> str | None:
        regime = self._regime_cache.get(symbol, "UNKNOWN")
        return f"Laya Market Regime: {regime}"
