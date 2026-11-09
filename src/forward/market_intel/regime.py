"""Pure classification rules for market regime measurements."""

from __future__ import annotations

from forward.core.errors import MarketHistoryUnavailable
from forward.market_intel.models import (
    DEFAULT_REGIME_CONFIG_V1,
    MarketRegimeClassification,
    RawMarketMeasurements,
    RegimeModelConfig,
)


def classify_regime_from_raw(
    raw: RawMarketMeasurements,
    config: RegimeModelConfig | None = None,
) -> MarketRegimeClassification:
    """Classify the market using recorded measurements and explicit thresholds.

    Args:
        raw: Point-in-time benchmark and market breadth measurements.
        config: Versioned thresholds used to classify the measurements. Uses the
            published default model when omitted.

    Returns:
        The market regime, confidence, and explanation for the classification.

    Raises:
        MarketHistoryUnavailable: If a required measurement is missing.
    """
    cfg = config if config is not None else DEFAULT_REGIME_CONFIG_V1
    required = {
        "NIFTY trend": raw.nifty_trend_pct,
        "market breadth": raw.breadth_above_ema50_pct,
        "volatility ratio": raw.volatility_ratio,
        "ATR percentage": raw.atr_pct,
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        raise MarketHistoryUnavailable(
            "Cannot classify market regime without " + ", ".join(missing)
        )
    trend = raw.nifty_trend_pct
    breadth = raw.breadth_above_ema50_pct
    vol_ratio = raw.volatility_ratio
    atr_pct = raw.atr_pct

    provenance_str = f"[{raw.benchmark_provenance.symbol} ({raw.benchmark_provenance.kind})]"

    if vol_ratio >= cfg.high_vol_ratio or atr_pct > cfg.high_vol_atr_pct:
        return MarketRegimeClassification(
            regime="HIGH_VOLATILITY",
            label="High Volatility Shock",
            confidence=0.90,
            raw_measurements=raw,
            regime_model_version=cfg.version,
            explanation=(
                f"Volatility expansion detected via {provenance_str}: ATR ratio "
                f"{vol_ratio:.2f}x ≥ {cfg.high_vol_ratio:.2f}x or ATR "
                f"{atr_pct:.2f}% > {cfg.high_vol_atr_pct:.2f}%"
            ),
            as_of=raw.as_of,
        )

    if vol_ratio <= cfg.low_vol_ratio and atr_pct < cfg.low_vol_atr_pct:
        return MarketRegimeClassification(
            regime="LOW_VOLATILITY",
            label="Low Volatility Range",
            confidence=0.85,
            raw_measurements=raw,
            regime_model_version=cfg.version,
            explanation=(
                f"Volatility compression detected via {provenance_str}: ATR ratio "
                f"{vol_ratio:.2f}x ≤ {cfg.low_vol_ratio:.2f}x and ATR "
                f"{atr_pct:.2f}% < {cfg.low_vol_atr_pct:.2f}%"
            ),
            as_of=raw.as_of,
        )

    if trend > cfg.bull_min_trend_pct and breadth >= cfg.bull_min_breadth_pct:
        return MarketRegimeClassification(
            regime="BULLISH_TREND",
            label="Bullish Trend",
            confidence=0.92,
            raw_measurements=raw,
            regime_model_version=cfg.version,
            explanation=(
                f"Benchmark trend is +{trend:.2f}% above SMA{cfg.trend_sma_window} "
                f"via {provenance_str} and breadth is {breadth:.1f}% "
                f"≥ {cfg.bull_min_breadth_pct:.1f}%"
            ),
            as_of=raw.as_of,
        )

    if trend < cfg.bear_max_trend_pct and breadth <= cfg.bear_max_breadth_pct:
        return MarketRegimeClassification(
            regime="BEARISH_TREND",
            label="Bearish Trend",
            confidence=0.88,
            raw_measurements=raw,
            regime_model_version=cfg.version,
            explanation=(
                f"Benchmark trend is {trend:.2f}% below SMA{cfg.trend_sma_window} "
                f"via {provenance_str} and breadth is {breadth:.1f}% "
                f"≤ {cfg.bear_max_breadth_pct:.1f}%"
            ),
            as_of=raw.as_of,
        )

    return MarketRegimeClassification(
        regime="SIDEWAYS",
        label="Sideways / Rangebound",
        confidence=0.80,
        raw_measurements=raw,
        regime_model_version=cfg.version,
        explanation=(
            f"Benchmark {provenance_str} trend ({trend:.2f}%) or breadth "
            f"({breadth:.1f}%) is within neutral bounds"
        ),
        as_of=raw.as_of,
    )
