"""Market regime classification through the configured Laya model.

The classifier returns only the model's result. A failed model load, failed
inference, or confidence below the configured minimum is an error. It never
substitutes a rule-based result for missing or uncertain model output.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel


@dataclass
class MarketRegimeVerdict:
    regime: str
    confidence: float
    source: str = "laya"
    raw_decision: Any = None


class _RegimeSchema(BaseModel):
    regime: Literal[
        "TRENDING_BULLISH", "TRENDING_BEARISH", "MEAN_REVERTING", "HIGH_VOLATILITY"
    ]


class LayaRegimeClassifier:
    """Classifies market regimes via indicator rules, with a low-weight Laya assist."""

    REGIMES = [
        "TRENDING_BULLISH",
        "TRENDING_BEARISH",
        "MEAN_REVERTING",
        "HIGH_VOLATILITY",
    ]

    def __init__(
        self,
        model_name: str = "convaiinnovations/laya",
        device: str | None = None,
        min_confidence: float = 0.35,
    ):
        if not 0.0 <= min_confidence <= 1.0:
            raise ValueError("min_confidence must be between 0 and 1")
        self.model_name = model_name
        self.device = device
        self.min_confidence = min_confidence
        self._agent = None

    def _load_agent(self):
        if self._agent is not None:
            return self._agent

        import laya

        self._agent = laya.load(self.model_name, device=self.device)
        return self._agent

    def classify_market_context(
        self,
        symbol: str,
        atr_pct: float,
        adx: float,
        rsi: float,
        return_20d: float,
        sma_ratio: float,
    ) -> MarketRegimeVerdict:
        """Evaluate market state indicators into a structured decision.

        Args:
            symbol: Ticker or index name (e.g. NIFTY50, RELIANCE).
            atr_pct: Average True Range as a percentage of close price.
            adx: Average Directional Index (trend strength, 0-100).
            rsi: Relative Strength Index (momentum, 0-100).
            return_20d: 20-period return in percentage (e.g. +3.5 or -2.1).
            sma_ratio: Fast SMA / Slow SMA ratio (e.g. 1.02 for +2% above slow SMA).
        """
        agent = self._load_agent()

        state = {
            "symbol": symbol,
            "atr_pct": round(float(atr_pct), 2),
            "trend_strength_adx": round(float(adx), 2),
            "momentum_rsi": round(float(rsi), 2),
            "return_20d_pct": round(float(return_20d), 2),
            "fast_to_slow_sma_ratio": round(float(sma_ratio), 4),
        }

        import laya

        result = laya.decide(agent, state, schema=_RegimeSchema, return_details=True)
        regime = result.values["regime"]
        confidence = float(result.confidence["regime"])
        if confidence < self.min_confidence:
            raise ValueError(
                f"Laya confidence {confidence:.2f} is below the configured "
                f"minimum {self.min_confidence:.2f} for {symbol}"
            )
        return MarketRegimeVerdict(
            regime=regime, confidence=confidence, source="laya", raw_decision=result
        )


# Global singleton classifier instance
_default_classifier = None


def get_regime_classifier() -> LayaRegimeClassifier:
    global _default_classifier
    if _default_classifier is None:
        _default_classifier = LayaRegimeClassifier()
    return _default_classifier
