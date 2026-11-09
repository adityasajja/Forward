"""Market regime classification API endpoint powered by Convai's Laya model."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from forward.api.deps import require_permission
from forward.auth.rbac import Permission
from forward.composition import get_market_regime_service

router = APIRouter(prefix="/api/v1/regime", tags=["Market Regime"])
_READ = require_permission(Permission.STRATEGY_READ)


class RegimeEvaluationRequest(BaseModel):
    symbol: str = Field(description="Asset or index identifier")
    atr_pct: float = Field(description="Average True Range as a % of close price")
    adx: float = Field(description="ADX trend strength (0-100)")
    rsi: float = Field(description="RSI momentum (0-100)")
    return_20d: float = Field(description="20-period return %")
    sma_ratio: float = Field(description="Fast SMA to Slow SMA ratio")


class RegimeEvaluationResponse(BaseModel):
    symbol: str
    regime: str
    confidence: float
    model: str
    recommended_strategy_types: list[str]


@router.post(
    "/evaluate",
    response_model=RegimeEvaluationResponse,
    dependencies=[Depends(_READ)],
)
def evaluate_market_regime(req: RegimeEvaluationRequest) -> RegimeEvaluationResponse:
    """Classifies current market indicators into a discrete market regime."""
    evaluation = get_market_regime_service().evaluate(
        symbol=req.symbol,
        atr_pct=req.atr_pct,
        adx=req.adx,
        rsi=req.rsi,
        return_20d=req.return_20d,
        sma_ratio=req.sma_ratio,
    )
    return RegimeEvaluationResponse(**evaluation)
