"""Ports used by the market-regime evaluation workflow."""

from __future__ import annotations

from typing import Protocol


class MarketRegimeDecision(Protocol):
    """The classifier result fields used by the application service."""

    regime: str
    confidence: float


class MarketRegimeClassifierPort(Protocol):
    """Classify market context without depending on an HTTP request."""

    model_name: str

    def classify_market_context(
        self,
        symbol: str,
        atr_pct: float,
        adx: float,
        rsi: float,
        return_20d: float,
        sma_ratio: float,
    ) -> MarketRegimeDecision:
        """Classify one symbol from its computed market indicators."""
