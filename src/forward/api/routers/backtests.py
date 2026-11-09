"""Backtest routes — ``/api/v1/backtests``.

The contract in ``docs/API_CONTRACT.md`` is::

    POST   /                       submit a run -> { run_id, status }
    GET    /{run_id}               status + progress
    GET    /{run_id}/metrics       the full metric set
    GET    /{run_id}/equity        equity + drawdown curves
    GET    /{run_id}/trades        paginated trade list
    GET    /{run_id}/trades/{seq}  one trade, with its signal conditions
    GET    /{run_id}/monthly       monthly/yearly return matrix
    GET    /                       run history
    POST   /{run_id}/cancel        stop a queued or running run
    GET    /options                everything the config form needs

``POST /{run_id}/monte-carlo`` resamples recorded net portfolio returns with a
seeded circular moving-block bootstrap. It reports conditional historical
outcomes, not forecasts, and defaults to 5,000 paths and five-observation blocks.

One contract entry remains absent:
* ``POST /{run_id}/walk-forward`` — the honest version already exists as
  ``/research`` in the main app, with deflated-Sharpe correction for the number
  of trials. A second, uncorrected walk-forward behind a backtest run id would be
  the flattering number and the wrong one.

``GET /{run_id}/exposure`` from the contract is folded into ``/equity``: the
three curves share a single read and a single round trip, and a client that has
to make three calls to draw one chart makes three calls.

Every route is gated on ``Permission.BACKTEST_RUN`` for the mutating actions and
``Permission.STRATEGY_READ`` for reading a run, matching the RBAC table rather
than inventing a permission here.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from forward.api.deps import CurrentPrincipal, require_permission
from forward.auth.rbac import Permission
from forward.composition import get_backtest_service
from forward.config.settings import get_settings
from forward.services.backtests import (
    STATUSES,
    BacktestAnalysisBusy,
    BacktestError,
)

router = APIRouter(prefix="/api/v1/backtests", tags=["backtests"])

_RUN = Depends(require_permission(Permission.BACKTEST_RUN))
_READ = Depends(require_permission(Permission.STRATEGY_READ))


class BootstrapRequest(BaseModel):
    paths: int = Field(default_factory=lambda: get_settings().backtest_uncertainty_paths, ge=get_settings().backtest_uncertainty_paths_min, le=get_settings().backtest_uncertainty_paths_max)
    block: int = Field(default_factory=lambda: get_settings().backtest_uncertainty_block, ge=get_settings().backtest_uncertainty_block_min, le=get_settings().backtest_uncertainty_block_max)
    seed: int = Field(default_factory=lambda: get_settings().backtest_uncertainty_seed, ge=0, le=4294967295)


@router.post("/{run_id}/monte-carlo", dependencies=[_RUN])
def monte_carlo(run_id: str, body: BootstrapRequest, principal: CurrentPrincipal):
    try:
        return get_backtest_service().monte_carlo(
            run_id,
            principal.user_id,
            paths=body.paths,
            block=body.block,
            seed=body.seed,
        )
    except BacktestAnalysisBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    except BacktestError as exc:
        raise _fail(exc) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except ImportError as exc:
        raise HTTPException(
            503, "Install the compute dependencies to run resampling analysis."
        ) from exc


def _fail(exc: BacktestError) -> HTTPException:
    return HTTPException(exc.status, detail={"detail": str(exc), "code": exc.code})


@router.get('/{run_id}/hold-benchmark', dependencies=[_READ])
def hold_benchmark(run_id: str, principal: CurrentPrincipal):
    service = get_backtest_service()
    try:
        return service.hold_benchmark(run_id, principal.user_id)
    except BacktestError as exc:
        raise _fail(exc) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


# --------------------------------------------------------------------- models
class SubmitRequest(BaseModel):
    """A run configuration.

    Loosely typed on purpose: the schema and its validation live in
    ``forward.backtest.config``, which is also what the CLI uses. A Pydantic mirror
    here would be a second definition of the same rules, and the two would drift
    — the API would accept a config the CLI rejects, or vice versa.
    """

    model_config = {"extra": "allow"}


class ConfigOptions(BaseModel):
    strategies: list[dict[str, Any]]
    timeframes: list[dict[str, Any]]
    cost_models: list[dict[str, Any]]
    sizing_modes: list[dict[str, Any]]
    rounding_rules: list[dict[str, Any]]
    sources: list[dict[str, Any]]
    exchanges: list[str]
    universes: list[dict[str, Any]]
    limits: dict[str, Any]


# ---------------------------------------------------------------------- routes
@router.get("/options", response_model=ConfigOptions, dependencies=[_READ])
def options(principal: CurrentPrincipal) -> dict[str, Any]:
    """Everything the configuration form needs, with availability flagged.

    Owner-scoped, and it must be: the strategy registry merges the built-ins
    with the caller's *saved* strategies, and those carry the versions a
    deployment pins. Resolving them for "the first user in the table" showed one
    account another account's strategies and hid its own — and because only
    saved strategies are deployable, it made the paper form look empty while the
    strategies existed. The caller's own rows are the only rows it may see.
    """
    return get_backtest_service().options(owner_user_id=principal.user_id)


@router.post("", status_code=status.HTTP_202_ACCEPTED, dependencies=[_RUN])
def submit(payload: SubmitRequest, principal: CurrentPrincipal) -> dict[str, Any]:
    """Submit a run and return immediately with its id.

    A backtest is CPU-bound and can take minutes. Running it inline would hold
    the request open, pin a worker thread, and give the browser nothing to show
    but a spinner with no way to tell progress from a hang. So the run is
    persisted as QUEUED and executed on a worker; the caller polls
    ``GET /{run_id}``.
    """
    try:
        return get_backtest_service().submit(payload.model_dump(), user_id=principal.user_id)
    except BacktestError as exc:
        raise _fail(exc) from exc


@router.post("/readiness", dependencies=[_RUN])
def readiness(payload: SubmitRequest, principal: CurrentPrincipal) -> dict[str, Any]:
    """Check cached candles without creating a backtest run."""
    try:
        return get_backtest_service().readiness(
            payload.model_dump(), user_id=principal.user_id
        )
    except BacktestError as exc:
        raise _fail(exc) from exc


@router.get("", dependencies=[_READ])
def history(
    principal: CurrentPrincipal,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=get_settings().page_sizes["backtest_default"], ge=1, le=get_settings().page_sizes["backtest_max"]),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    try:
        return get_backtest_service().history(
            principal.user_id, status=status_filter, limit=limit, offset=offset
        )
    except BacktestError as exc:
        raise _fail(exc) from exc


@router.get("/statuses")
def statuses() -> dict[str, Any]:
    """The lifecycle a client can expect, so a UI need not hardcode it."""
    return {"statuses": list(STATUSES)}


@router.get("/{run_id}", dependencies=[_READ])
def run_detail(run_id: str, principal: CurrentPrincipal) -> dict[str, Any]:
    """Status, progress and — once finished — the config and metrics.

    Progress is coarse by design. The engine exposes no per-bar callback, and a
    smooth percentage driven by a timer would be a progress bar that reports the
    passage of time rather than the state of the work.
    """
    try:
        return get_backtest_service().run(run_id, principal.user_id)
    except BacktestError as exc:
        raise _fail(exc) from exc


@router.get("/{run_id}/metrics", dependencies=[_READ])
def metrics(run_id: str, principal: CurrentPrincipal) -> dict[str, Any]:
    try:
        return get_backtest_service().metrics(run_id, principal.user_id)
    except BacktestError as exc:
        raise _fail(exc) from exc


@router.get("/{run_id}/equity", dependencies=[_READ])
def equity(run_id: str, principal: CurrentPrincipal) -> dict[str, Any]:
    """Equity, drawdown and exposure curves in one response."""
    try:
        return get_backtest_service().equity(run_id, principal.user_id)
    except BacktestError as exc:
        raise _fail(exc) from exc


@router.get("/{run_id}/trades", dependencies=[_READ])
def trades(
    run_id: str,
    principal: CurrentPrincipal,
    limit: int = Query(default=get_settings().page_sizes["backtest_trade_default"], ge=1, le=get_settings().page_sizes["backtest_trade_max"]),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    try:
        return get_backtest_service().trades(
            run_id, principal.user_id, limit=limit, offset=offset
        )
    except BacktestError as exc:
        raise _fail(exc) from exc


@router.get("/{run_id}/trades/{seq}", dependencies=[_READ])
def trade_detail(
    run_id: str, seq: int, principal: CurrentPrincipal
) -> dict[str, Any]:
    """One trade with its strategy version, exits, costs and signal conditions."""
    try:
        return get_backtest_service().trade(run_id, principal.user_id, seq)
    except BacktestError as exc:
        raise _fail(exc) from exc


@router.get("/{run_id}/monthly", dependencies=[_READ])
def monthly(run_id: str, principal: CurrentPrincipal) -> dict[str, Any]:
    try:
        return get_backtest_service().monthly(run_id, principal.user_id)
    except BacktestError as exc:
        raise _fail(exc) from exc


@router.post("/{run_id}/cancel", dependencies=[_RUN])
def cancel(run_id: str, principal: CurrentPrincipal) -> dict[str, Any]:
    """Mark a run cancelled so its result is discarded.

    This stops the *result* being recorded; the worker thread already running
    is not interrupted. Cooperative cancellation would need the engine to poll a
    flag inside its bar loop, which is an engine change this work is explicitly
    not making. The repository guard is what makes it safe: a cancelled run's
    completion write is rejected and its artefacts are removed.
    """
    try:
        return get_backtest_service().cancel(run_id, principal.user_id)
    except BacktestError as exc:
        raise _fail(exc) from exc


__all__ = ["router"]
