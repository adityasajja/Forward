"""Strategy list and the research (walk-forward) endpoint."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from forward.api.deps import get_principal
from forward.api.legacy.common import _authed_client, _clean, _equity_points, _history_feed
from forward.config.settings import get_settings

router = APIRouter()


class ScreeningRequest(BaseModel):
    days: int = Field(
        default_factory=lambda: int(
            get_settings().parameter_screen_config["default_days"]
        ),
        ge=get_settings().parameter_screen_config["minimum_days"],
        le=get_settings().parameter_screen_config["maximum_days"],
    )
    max_symbols: int = Field(
        default_factory=lambda: int(
            get_settings().parameter_screen_config["default_symbols"]
        ),
        ge=get_settings().parameter_screen_config["minimum_symbols"],
        le=get_settings().parameter_screen_config["maximum_symbols"],
    )


@router.post("/research/screen", dependencies=[Depends(get_principal)])
def screen_parameters(body: ScreeningRequest) -> dict[str, Any]:
    """Bounded, CUDA-accelerated candidate screen; never portfolio evidence."""
    from forward.composition import get_parameter_screen_service
    from forward.services.parameter_screen import ScreeningInProgressError

    try:
        return get_parameter_screen_service().screen(body.days, body.max_symbols)
    except ScreeningInProgressError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except (ImportError, FileNotFoundError) as exc:
        raise HTTPException(
            503, "Screening requires the compute dependencies and cached daily prices."
        ) from exc


# ----------------------------------------------------------------------
# Research — walk-forward validation
# ----------------------------------------------------------------------
class ResearchRequest(BaseModel):
    strategy: str = "signals_entry"
    #: ``cache``  — local parquet dailies (fast, no session, ~1 year)
    #: ``fetch``  — pull deep history from IIFL (needs a session, years of data)
    #: ``synthetic`` — random walks; only useful for smoke-testing the harness
    source: str = "cache"
    symbols: list[str] = Field(default_factory=list)
    exchange: str = "NSEEQ"
    cash: float = 1_000_000.0
    train: int | None = None
    test: int | None = None
    step: int | None = None
    warmup: int | None = None
    lookback_days: int = 2200
    fast: list[int] = Field(default_factory=lambda: [10, 20, 30])
    slow: list[int] = Field(default_factory=lambda: [50, 100])
    search: bool = False
    min_trades: int = 20
    min_folds: int = 3
    confidence: float = 0.95
    slippage_bps: float = 5.0


def _fetch_feed(symbols: list[str], exchange: str, lookback_days: int):
    """Deep daily history pulled from IIFL, one symbol at a time.

    Needed because the local cache holds about a year, which cannot be carved
    into folds that also leave room for indicator warmup.
    """
    from forward.wiring.research import build_research_history_service

    client = _authed_client()
    wanted = [symbol.upper() for symbol in symbols]
    service = build_research_history_service(client)
    try:
        return service.load_daily_feed(wanted, exchange, lookback_days)
    except ValueError as error:
        raise HTTPException(
            503,
            "no daily history returned for any requested symbol — check the "
            "session and that the symbols exist on this exchange",
        ) from error


@router.get("/strategies")
def strategies() -> dict[str, Any]:
    """Every strategy the engine can run, with its validation status.

    ``validation`` is attached from the last walk-forward run rather than left
    to the caller. A registry that lists an unvalidated strategy next to a
    validated one, with nothing to tell them apart, invites exactly the mistake
    this project is built to avoid.
    """
    from forward.wiring.research import get_research_catalog_service

    return get_research_catalog_service().list_strategies()


@router.post("/research", dependencies=[Depends(get_principal)])
def research(request: ResearchRequest) -> dict[str, Any]:
    """Walk a strategy forward and report only the out-of-sample evidence.

    This is the endpoint to trust. ``/backtest`` scores one configuration on
    data it may already have seen; this one picks parameters on a training
    window, scores them once on the following unseen window, and then asks
    whether the winner clears a Sharpe hurdle scaled to how many
    combinations were tried.
    """
    from forward.services.research_validation import (
        ResearchRunConfig,
        ResearchValidationError,
    )
    from forward.wiring.research import (
        build_synthetic_research_feed,
        get_research_catalog_service,
        get_research_validation_service,
    )

    catalog = get_research_catalog_service()
    strategy_cls = catalog.strategy_class(request.strategy)
    if strategy_cls is None:
        raise HTTPException(
            400,
            f"unknown strategy: {request.strategy} (have {catalog.strategy_names()})",
        )

    source = request.source
    if source not in {"cache", "fetch", "synthetic"}:
        raise HTTPException(400, f"unknown source: {request.source}")

    symbols = [s.strip().upper() for s in request.symbols if s.strip()]
    if source == "synthetic":
        if not symbols:
            raise HTTPException(400, "synthetic research requires at least one explicit symbol")
        feed = build_synthetic_research_feed(tuple(symbols))
        used = symbols
    elif source == "fetch":
        feed, used = _fetch_feed(symbols, request.exchange.upper(), request.lookback_days)
    else:
        feed, used = _history_feed(symbols, request.exchange.upper())

    try:
        payload = get_research_validation_service().run(
            ResearchRunConfig(
                strategy=request.strategy,
                source=source,
                cash=request.cash,
                train=request.train,
                test=request.test,
                step=request.step,
                warmup=request.warmup,
                fast=request.fast,
                slow=request.slow,
                search=request.search,
                min_trades=request.min_trades,
                min_folds=request.min_folds,
                confidence=request.confidence,
                slippage_bps=request.slippage_bps,
            ),
            strategy_class=strategy_cls,
            feed=feed,
            symbols=used,
        )
    except ResearchValidationError as exc:
        raise HTTPException(422, str(exc)) from exc
    payload["equity"] = _equity_points(payload.pop("equity_series"))
    payload["benchmark_equity"] = _equity_points(payload.pop("benchmark_equity_series"))
    return _clean(payload)
