"""Run a backtest."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from forward.api.deps import get_principal
from forward.api.legacy.common import _clean, _equity_points, _history_feed

logger = logging.getLogger("forward.api")

router = APIRouter()


class BacktestRequest(BaseModel):
    strategy: str = "sma_crossover"
    symbols: list[str] = Field(default_factory=lambda: ["AAPL", "MSFT"])
    initial_cash: float = 1_000_000.0
    fast: int = 20
    slow: int = 50
    slippage_bps: float = 5.0
    square_off_eod: bool = False
    futures: bool = False
    #: ``synthetic`` generates random walks (fast, but meaningless as evidence);
    #: ``history`` replays the real cached NSE dailies.
    source: str = "synthetic"
    exchange: str = "NSEEQ"


@router.post("/backtest", dependencies=[Depends(get_principal)])
def run_backtest(request: BacktestRequest) -> dict[str, Any]:
    """Score one configuration on one dataset.

    In-sample by construction: the parameters you pass were chosen by you,
    usually after seeing a result like this one. Use ``/research`` for the
    number you would actually act on.
    """
    from forward.composition import (
        build_synthetic_backtest_feed,
        get_backtest_service,
    )

    service = get_backtest_service()
    if not service.supports_preview_strategy(request.strategy):
        raise HTTPException(400, f"unknown strategy: {request.strategy}")

    symbols = [s.strip().upper() for s in request.symbols if s.strip()]
    if request.source == "history":
        if not symbols:
            raise HTTPException(400, "pass `symbols` when source=history")
        feed, used = _history_feed(symbols, request.exchange.upper())
    else:
        used = symbols or ["AAPL", "MSFT"]
        feed = build_synthetic_backtest_feed(
            symbols=tuple(used),
            start=datetime(2024, 1, 1, 9, 30),
            end=datetime(2024, 6, 28, 15, 59),
            futures=request.futures,
        )

    result = service.preview(
        strategy_name=request.strategy,
        feed=feed,
        initial_cash=request.initial_cash,
        fast=request.fast,
        slow=request.slow,
        slippage_bps=request.slippage_bps,
        square_off_eod=request.square_off_eod,
    )
    return _clean(
        {
            "strategy": result.strategy_name,
            "source": request.source,
            "symbols": used,
            "metrics": result.metrics.as_dict(),
            "num_fills": int(len(result.fills)),
            "num_trades": int(len(result.trades)),
            "killed": result.killed,
            "kill_reason": result.kill_reason,
            "equity": _equity_points(result.equity),
            "trades": result.trades.head(200).to_dict(orient="records"),
        }
    )
