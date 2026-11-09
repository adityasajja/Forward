"""Application workflow for market-regime evaluation."""

from __future__ import annotations

from typing import Any

from forward.core.market_regime_ports import MarketRegimeClassifierPort

_STRATEGIES_BY_REGIME = {
    "TRENDING_BULLISH": [
        "MomentumBreakout",
        "SmaCrossover",
        "JegadeeshTitmanMomentum",
    ],
    "TRENDING_BEARISH": ["CashPreservation", "ShortHorizonReversal"],
    "MEAN_REVERTING": ["AvellanedaLeeMeanReversion", "SehgalLowVolAnomaly"],
    "HIGH_VOLATILITY": ["VolatilityBreakout", "VolatilityManaged"],
}


class MarketRegimeService:
    """Classify market inputs and shape the regime response."""

    def __init__(self, classifier: MarketRegimeClassifierPort) -> None:
        """Create the service.

        Args:
            classifier: Configured market-regime classification engine.
        """
        self._classifier = classifier

    def evaluate(
        self,
        *,
        symbol: str,
        atr_pct: float,
        adx: float,
        rsi: float,
        return_20d: float,
        sma_ratio: float,
    ) -> dict[str, Any]:
        """Return the regime, confidence, model, and suggested strategies.

        Args:
            symbol: Asset or index identifier.
            atr_pct: Average True Range as a percentage of close.
            adx: Average Directional Index trend strength.
            rsi: Relative Strength Index momentum.
            return_20d: Return over the latest 20 periods.
            sma_ratio: Fast moving average divided by the slow average.

        Returns:
            Regime evaluation fields used by the API response model.
        """
        decision = self._classifier.classify_market_context(
            symbol=symbol,
            atr_pct=atr_pct,
            adx=adx,
            rsi=rsi,
            return_20d=return_20d,
            sma_ratio=sma_ratio,
        )
        return {
            "symbol": symbol,
            "regime": decision.regime,
            "confidence": decision.confidence,
            "model": self._classifier.model_name,
            "recommended_strategy_types": _STRATEGIES_BY_REGIME.get(
                decision.regime, []
            ),
        }
