"""Saved, configuration-driven F&O strategy authoring routes."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from forward.api.deps import CurrentPrincipal, require_permission
from forward.auth.models import Principal
from forward.auth.rbac import Permission
from forward.config.settings import get_settings
from forward.services.options_strategies import (
    InvalidOptionsConfiguration,
    InvalidOptionsStrategy,
)
from forward.services.strategies import StrategyError
from forward.wiring.options import (
    get_options_forward_service,
    get_options_market_service,
    get_options_run_service,
    get_options_strategy_service,
)

router = APIRouter(prefix="/api/v1/options", tags=["options"])

_READ = Depends(require_permission(Permission.STRATEGY_READ))
_WRITE = Depends(require_permission(Permission.STRATEGY_WRITE))
_PAPER_STOP = Depends(require_permission(Permission.ALGO_STOP))


class OptionsStrategyCreate(BaseModel):
    """Create an options strategy and its initial immutable version."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=4000)
    options: dict[str, Any]
    change_note: str | None = Field(default=None, max_length=1000)


class OptionsVersionCreate(BaseModel):
    """Append an immutable version to an options strategy."""

    model_config = ConfigDict(extra="forbid")

    options: dict[str, Any]
    change_note: str | None = Field(default=None, max_length=1000)


class OptionsValidate(BaseModel):
    """Validate a draft options configuration without saving it."""

    model_config = ConfigDict(extra="forbid")

    options: dict[str, Any]


class OptionsBacktestRequest(BaseModel):
    """Run a pinned options version on one imported historical dataset."""

    model_config = ConfigDict(extra="forbid")

    margin_mode: Literal["enforced", "pnl_only"]
    fill_on_next_open: bool
    allow_short: bool

    strategy_id: str
    version: int = Field(gt=0)
    dataset_id: str
    initial_cash: float = Field(gt=0)
    commission_per_contract: float = Field(ge=0)
    commission_minimum: float = Field(ge=0)
    transaction_cost_pct: float = Field(ge=0, le=get_settings().options_fee_percent_max)
    commission_cap_pct: float = Field(gt=0, le=get_settings().options_fee_percent_max)
    slippage_bps: float = Field(ge=0, le=get_settings().options_slippage_max_bps)


class OptionsForwardStart(BaseModel):
    """Start a live-data options simulation with no broker order submission."""

    model_config = ConfigDict(extra="forbid")

    strategy_id: str
    version: int = Field(gt=0)
    initial_cash: float = Field(gt=0)
    commission_per_contract: float = Field(ge=0)
    commission_minimum: float = Field(ge=0)
    transaction_cost_pct: float = Field(ge=0, le=get_settings().options_fee_percent_max)
    commission_cap_pct: float = Field(gt=0, le=get_settings().options_fee_percent_max)


def _utcnow() -> datetime:
    """Return an aware UTC timestamp for option run records."""
    return datetime.now(UTC).replace(tzinfo=None)


@router.post("/datasets", status_code=status.HTTP_201_CREATED)
async def import_options_dataset(
    request: Request,
    name: str = Query(min_length=1, max_length=128),
    principal: Principal = Depends(require_permission(Permission.BACKTEST_RUN)),
) -> dict[str, Any]:
    """Import a ZIP containing ``bars.csv`` and ``instruments.json``.

    Bars require ``ts,symbol,open,high,low,close`` and optional volume. The JSON
    file is an array of full instrument records, including the dated options
    contract metadata. No synthetic bars or substitute contract files are used.
    """
    from forward.config.settings import get_settings

    settings = get_settings()
    limit = settings.options_dataset_max_bytes
    declared_size = request.headers.get("content-length")
    if declared_size is not None and int(declared_size) > limit:
        raise HTTPException(413, f"Dataset archive exceeds the configured {limit}-byte limit.")
    archive = await request.body()
    if len(archive) > limit:
        raise HTTPException(413, f"Dataset archive exceeds the configured {limit}-byte limit.")
    try:
        record = get_options_run_service().import_dataset(
            principal.user_id,
            name,
            archive,
            settings.options_dataset_dir,
            limit,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {
        "dataset_id": record["dataset_id"],
        "name": record["name"],
        "bars": record["bar_count"],
        "symbols": record["symbol_count"],
        "format": {"bars": "long-format OHLCV CSV", "instruments": "full dated contract JSON"},
    }


def _run_options_backtest(run_id: str, user_id: str, request_data: dict[str, Any]) -> None:
    """Execute the shared options backtest use case in a background worker."""
    from forward.wiring.options import get_options_backtest_service

    get_options_backtest_service().run(run_id, user_id, request_data)


@router.post("/backtests", status_code=status.HTTP_202_ACCEPTED)
def start_options_backtest(
    body: OptionsBacktestRequest,
    background_tasks: BackgroundTasks,
    principal: Principal = Depends(require_permission(Permission.BACKTEST_RUN)),
) -> dict[str, Any]:
    """Queue a backtest for an exact saved options version and imported dataset."""
    try:
        get_options_strategy_service().get_version(
            principal.user_id, body.strategy_id, body.version
        )
    except InvalidOptionsStrategy as exc:
        raise HTTPException(422, str(exc)) from exc
    except StrategyError as exc:
        raise _fail(exc) from exc
    if get_options_run_service().dataset(principal.user_id, body.dataset_id) is None:
        raise HTTPException(404, "Options dataset not found for this account.")
    run_id = uuid.uuid4().hex
    request_data = body.model_dump()
    get_options_run_service().create_backtest(
        dict(
            run_id=run_id,
            user_id=principal.user_id,
            strategy_id=body.strategy_id,
            strategy_version=body.version,
            dataset_id=body.dataset_id,
            status="QUEUED",
            config=json.dumps(request_data),
        )
    )
    background_tasks.add_task(_run_options_backtest, run_id, principal.user_id, request_data)
    return {"run_id": run_id, "status": "QUEUED"}


@router.get("/datasets", dependencies=[_READ])
def list_options_datasets(principal: CurrentPrincipal) -> dict[str, Any]:
    """List this account's imported option datasets for backtest selection."""
    rows = get_options_run_service().list_datasets(principal.user_id)
    return {
        "datasets": [
            {
                "dataset_id": row["dataset_id"],
                "name": row["name"],
                "bars": row["bar_count"],
                "symbols": row["symbol_count"],
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            }
            for row in rows
        ]
    }


@router.get("/backtests/{run_id}", dependencies=[_READ])
def get_options_backtest(run_id: str, principal: CurrentPrincipal) -> dict[str, Any]:
    """Read an owned F&O backtest run, including its result when complete."""
    row = get_options_run_service().backtest(run_id, principal.user_id)
    if row is None:
        raise HTTPException(404, "F&O backtest not found.")
    return row


def _persist_forward_finish(run) -> None:
    """Persist the simulated ledger when its worker exits."""
    snapshot = run.snapshot()
    positions_open = any(
        abs(float(position["quantity"])) > 1e-9 for position in snapshot["positions"]
    )
    state = "FAILED" if run.error else "FLATTEN_FAILED" if positions_open else "STOPPED"
    get_options_run_service().update_forward_run(
        run.run_id,
        run.user_id,
        status=state,
        snapshot=json.dumps(snapshot),
        error=run.error,
        finished_at=_utcnow(),
    )


@router.post("/forward-runs", status_code=status.HTTP_202_ACCEPTED)
def start_options_forward_run(
    body: OptionsForwardStart,
    principal: Principal = Depends(require_permission(Permission.ALGO_START)),
) -> dict[str, Any]:
    """Start a simulated F&O run driven by current IIFL market quotes."""
    from forward.composition import get_risk_state_service
    from forward.services.strategies import StrategyError

    try:
        strategy_config = get_options_strategy_service().config_for_version(
            principal.user_id, body.strategy_id, body.version
        )
    except InvalidOptionsStrategy as exc:
        raise HTTPException(422, str(exc)) from exc
    except StrategyError as exc:
        raise _fail(exc) from exc
    market_service = get_options_market_service()
    session = market_service.session()
    if session is None:
        raise HTTPException(409, "Authenticate with IIFL before starting forward paper testing.")
    try:
        market_inputs = market_service.prepare_forward(
            session=session,
            underlying=strategy_config.underlying,
            exchange=strategy_config.exchange,
            spot_symbol=strategy_config.spot_symbol,
            spot_exchange=strategy_config.spot_exchange,
        )
        from forward.api.stream import get_broadcaster

        broadcaster = get_broadcaster()
        topic = market_service.bridge_topic(market_inputs.spot)
        if not broadcaster.ensure_indices(((market_inputs.spot.symbol, topic),)):
            raise RuntimeError("the shared IIFL live bridge is unavailable")
        bridge_client = broadcaster.live_feed_connection()
        if bridge_client is None:
            raise RuntimeError("the shared IIFL live feed disconnected")
    except Exception as exc:  # noqa: BLE001 - no substitute metadata or spot source
        raise HTTPException(409, f"Options contract or spot catalog is unavailable: {exc}") from exc

    run_id = uuid.uuid4().hex
    config = body.model_dump()
    get_options_run_service().create_forward_run(
        dict(
            run_id=run_id,
            user_id=principal.user_id,
            strategy_id=body.strategy_id,
            strategy_version=body.version,
            status="STARTING",
            config=json.dumps(config),
        )
    )
    try:
        risk_service = get_risk_state_service()
        run = get_options_forward_service().start(
            run_id=run_id,
            user_id=principal.user_id,
            session=market_inputs.session,
            instruments={market_inputs.spot.symbol: market_inputs.spot},
            catalog=market_inputs.catalog,
            definition=strategy_config.model_dump(mode="json"),
            initial_cash=body.initial_cash,
            commission_per_contract=body.commission_per_contract,
            commission_minimum=body.commission_minimum,
            transaction_cost_pct=body.transaction_cost_pct,
            commission_cap_pct=body.commission_cap_pct,
            risk=risk_service.limits_with_daily_loss_cap(strategy_config.max_loss),
            kill_switch_provider=risk_service.kill_switch_engaged,
            on_finish=_persist_forward_finish,
            bridge_client=bridge_client,
        )
    except Exception as exc:  # noqa: BLE001 - store the failed start for the user
        get_options_run_service().update_forward_run(
            run_id, principal.user_id, status="FAILED", error=str(exc), finished_at=_utcnow()
        )
        raise HTTPException(409, f"Could not start options paper runner: {exc}") from exc
    return {"run_id": run.run_id, "status": "RUNNING", "execution": "simulated_quotes_only"}


@router.get("/forward-runs/{run_id}", dependencies=[_READ])
def get_options_forward_run(run_id: str, principal: CurrentPrincipal) -> dict[str, Any]:
    """Read the owned live-data simulation state and its paper positions."""
    payload = get_options_run_service().forward_run(run_id, principal.user_id)
    if payload is None:
        raise HTTPException(404, "F&O forward run not found.")
    active = get_options_forward_service().get(run_id, principal.user_id)
    if active and active.thread.is_alive():
        payload["status"] = "RUNNING"
        payload["snapshot"] = active.snapshot()
        if active.error:
            payload["error"] = active.error
        get_options_run_service().update_forward_run(
            run_id, principal.user_id, snapshot=json.dumps(payload["snapshot"])
        )
    elif payload["status"] in {"STARTING", "RUNNING", "STOPPING"}:
        payload["status"] = "INTERRUPTED"
        payload["error"] = "The app process restarted before this paper run completed."
        get_options_run_service().update_forward_run(
            run_id,
            principal.user_id,
            status="INTERRUPTED",
            error=payload["error"],
            finished_at=_utcnow(),
        )
    return payload


@router.get("/forward-runs", dependencies=[_READ])
def list_options_forward_runs(principal: CurrentPrincipal, limit: int = get_settings().page_sizes["options_forward_default"]) -> dict[str, Any]:
    """List recent F&O paper runs for the signed-in account."""
    maximum = get_settings().page_sizes["options_forward_max"]
    if not 1 <= limit <= maximum:
        raise HTTPException(422, f"limit must be between 1 and {maximum}")
    return {"runs": get_options_run_service().list_forward_runs(principal.user_id, limit)}


@router.post("/forward-runs/{run_id}/stop", dependencies=[_PAPER_STOP])
def stop_options_forward_run(run_id: str, principal: CurrentPrincipal) -> dict[str, Any]:
    """Stop a paper run and submit simulated closing fills for its positions."""
    active = get_options_forward_service().stop(run_id, principal.user_id)
    if active is None:
        if not get_options_run_service().owns_forward_run(run_id, principal.user_id):
            raise HTTPException(404, "F&O forward run not found.")
        raise HTTPException(
            409,
            "This run is not active in this app process and cannot be stopped safely.",
        )
    get_options_run_service().update_forward_run(run_id, principal.user_id, status="STOPPING")
    return {"run_id": run_id, "status": "STOPPING", "flatten": "simulated quote fills"}


def _fail(exc: StrategyError) -> HTTPException:
    """Translate a strategy-service refusal into its stable API response."""
    detail: dict[str, Any] = {"detail": str(exc), "code": exc.code}
    if exc.detail:
        detail.update(exc.detail)
    return HTTPException(exc.status, detail=detail)


def _invalid_options(exc: InvalidOptionsConfiguration) -> HTTPException:
    """Build the same validation response for initial and later versions."""
    return HTTPException(422, detail={"detail": str(exc), **exc.report})


@router.get("/strategies", dependencies=[_READ])
def list_options_strategies(principal: CurrentPrincipal) -> dict[str, Any]:
    """List the caller's saved options strategies and their immutable versions."""
    strategies = get_options_strategy_service().list_for_user(principal.user_id)
    return {"strategies": strategies, "total": len(strategies)}


@router.post("/strategies", status_code=status.HTTP_201_CREATED)
def create_options_strategy(
    body: OptionsStrategyCreate,
    principal: Principal = Depends(require_permission(Permission.STRATEGY_WRITE)),
) -> dict[str, Any]:
    """Save an options strategy as data, without adding a Python strategy class."""
    try:
        return get_options_strategy_service().create(
            principal.user_id,
            name=body.name,
            description=body.description,
            options=body.options,
            change_note=body.change_note,
        )
    except InvalidOptionsConfiguration as exc:
        raise _invalid_options(exc) from exc
    except StrategyError as exc:
        raise _fail(exc) from exc


@router.post("/strategies/{strategy_id}/versions", status_code=status.HTTP_201_CREATED)
def create_options_version(
    strategy_id: str,
    body: OptionsVersionCreate,
    principal: Principal = Depends(require_permission(Permission.STRATEGY_WRITE)),
) -> dict[str, Any]:
    """Append a validated options configuration as a new immutable version."""
    try:
        return get_options_strategy_service().create_version(
            principal.user_id,
            strategy_id,
            options=body.options,
            change_note=body.change_note,
        )
    except InvalidOptionsStrategy as exc:
        raise HTTPException(422, str(exc)) from exc
    except InvalidOptionsConfiguration as exc:
        raise _invalid_options(exc) from exc
    except StrategyError as exc:
        raise _fail(exc) from exc


@router.post("/validate", dependencies=[_READ])
def validate_options(body: OptionsValidate) -> dict[str, Any]:
    """Check the generic options schema before the user saves an immutable version."""
    try:
        return get_options_strategy_service().validate_draft(body.options)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            422,
            detail={"detail": str(exc), "code": "invalid_options_definition"},
        ) from exc


@router.post("/contract-master/refresh", dependencies=[_WRITE])
def refresh_contract_master() -> dict[str, Any]:
    """Refresh the exchange contract file used to resolve listed option contracts."""
    try:
        return get_options_market_service().refresh_contract_master()
    except LookupError as exc:
        raise HTTPException(
            409,
            "Authenticate with IIFL before refreshing the options contract master.",
        ) from exc
    except Exception as exc:  # noqa: BLE001 - report the source failure; do not substitute stale metadata
        raise HTTPException(
            502, f"Could not refresh the IIFL options contract master: {exc}"
        ) from exc


@router.get("/contract-master", dependencies=[_READ])
def contract_master_status() -> dict[str, Any]:
    """Report cached F&O contract coverage without querying another source."""
    try:
        return get_options_market_service().contract_master_status()
    except Exception as exc:  # noqa: BLE001 - surface missing local catalog as unavailable
        return {"available": False, "contracts": 0, "underlyings": [], "reason": str(exc)}
