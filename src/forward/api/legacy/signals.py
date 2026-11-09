"""Trade signals and the self-learning status."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from forward.api.deps import get_principal, require_permission
from forward.api.legacy.access import (
    live_broker,
    order_principal,
    require_live_execution,
    require_order_permission,
)
from forward.auth.rbac import Permission

router = APIRouter()


@router.get("/self-learning/status", dependencies=[Depends(require_permission(Permission.STRATEGY_READ))])
def self_learning_status() -> dict[str, Any]:
    """Returns the market regime, dynamic strategy weights, and training metrics."""
    from dataclasses import asdict

    from forward.wiring.self_learning import get_self_learning_engine

    engine = get_self_learning_engine()
    return {
        "market_regime": asdict(engine.state.market_regime),
        "strategies": {k: asdict(v) for k, v in engine.state.strategies.items()},
        "total_cycles_trained": engine.state.total_cycles_trained,
        "last_trained_at": engine.state.last_trained_at,
        "model_version": engine.state.model_version,
    }


@router.post("/self-learning/train", dependencies=[Depends(get_principal)])
def self_learning_train() -> dict[str, Any]:
    """Triggers an online learning cycle across historical data."""
    from forward.wiring.self_learning import get_self_learning_training_service

    return get_self_learning_training_service().train()


@router.get("/trade-signals/settings", dependencies=[Depends(require_permission(Permission.RISK_READ))])
def trade_signals_settings_get() -> dict[str, Any]:
    """Return current position sizing + stop-loss configuration."""
    from forward.wiring.alerts import get_trade_signal_settings

    return get_trade_signal_settings().load().model_dump()


@router.put(
    "/trade-signals/settings",
    dependencies=[Depends(require_permission(Permission.RISK_CONFIGURE))],
)
def trade_signals_settings_put(body: dict[str, Any]) -> dict[str, Any]:
    """Update position sizing + stop-loss configuration."""
    from forward.wiring.alerts import get_trade_signal_settings

    return get_trade_signal_settings().update(body).model_dump()


@router.get("/trade-signals/setup-stats", dependencies=[Depends(require_permission(Permission.STRATEGY_READ))])
def trade_signals_setup_stats() -> dict[str, Any]:
    """Backtested hit-rate and median days-to-target, per setup — see `forward.signals_stats`.

    Empty right after a fresh start: the replay takes ~60s and is warmed on a
    background thread rather than blocking the request that first asks for it.
    """
    from dataclasses import asdict

    from forward.wiring.alerts import get_setup_stats_service

    return {setup: asdict(stat) for setup, stat in get_setup_stats_service().get().items()}


@router.get("/trade-signals", dependencies=[Depends(require_permission(Permission.STRATEGY_READ))])
def trade_signals_list(status: str | None = None) -> dict[str, Any]:
    """Return the signal queue. Pass ?status=PENDING|ACTIVE|DONE|SKIPPED to filter."""
    from forward.wiring.alerts import get_trade_signal_queue

    q = get_trade_signal_queue()
    signals = q.all()
    if status:
        signals = [s for s in signals if s.status == status.upper()]
    return {
        "signals": [s.model_dump(mode="json") for s in signals[:100]],
        "pending": len(q.pending()),
        "active": len(q.active()),
    }


@router.post("/trade-signals/scan", dependencies=[Depends(get_principal)])
def trade_signals_scan() -> dict[str, Any]:
    """Combine intelligent rules and quantitative research in one signal scan."""
    from forward.wiring.alerts import get_trade_signal_scan_service

    return get_trade_signal_scan_service().run()


@router.post("/trade-signals/{signal_id}/execute")
def trade_signals_execute(signal_id: str, http_request: Request) -> dict[str, Any]:
    """Approve a pending signal — places the entry, the stop-loss and the target.

    Rewired onto the OMS and the execution service on 2026-09-14. Three things
    were wrong with the previous version, and all three are why it now shares one
    order path with everything else:

    * ``OrderType.SL_MARKET`` **did not exist**. The route placed the entry order
      at the exchange and *then* raised ``AttributeError`` on the stop-loss leg,
      returning a 500 and leaving a live position with no stop and no target —
      the single most dangerous state this platform can produce.
    * Even had the name existed, that leg set ``limit_price`` and a
      ``broker_params["triggerPrice"]`` that nothing reads. ``IiflBroker`` takes
      the trigger from ``order.stop_price``, so the stop would have reached the
      exchange with no trigger price.
    * There was no idempotency guard, so clicking Execute twice placed the bracket
      twice.

    Each leg now carries a key derived from ``(signal_id, leg_index)``, so a retry
    — whether a double click, a timeout the browser retried, or an operator
    re-sending — returns the order that already exists instead of placing a
    second bracket.
    """
    from forward.composition import build_live_execution_service
    from forward.services.trade_signal_execution import (
        SignalNotFoundError,
        SignalNotPendingError,
        SignalOrderValidationError,
        SignalVenueFailure,
    )
    from forward.wiring.alerts import build_trade_signal_execution_service

    principal = order_principal(http_request)
    require_order_permission(principal)

    service = build_trade_signal_execution_service()
    try:
        signal = service.get_pending_signal(signal_id)
    except SignalNotFoundError as error:
        raise HTTPException(404, str(error)) from error
    except SignalNotPendingError as error:
        raise HTTPException(400, str(error)) from error

    # Paper mode check — last gate before anything is transmitted. The kill switch
    # is no longer checked here: it lives in the OMS risk gate, so it applies to
    # this route and to every other one without each having to remember.
    require_live_execution("Signal execution")

    broker = live_broker()
    execution = build_live_execution_service(broker, user_id=principal.user_id)
    try:
        return service.execute(
            signal_id=signal_id,
            signal=signal,
            user_id=principal.user_id,
            username=principal.username,
            execution=execution,
        )
    except SignalOrderValidationError as error:
        raise HTTPException(400, str(error)) from error
    except SignalVenueFailure as error:
        raise HTTPException(
            502,
            detail={
                "detail": str(error),
                "code": "venue_failed",
                "placed": error.placed,
            },
        ) from error


@router.post("/trade-signals/{signal_id}/skip", dependencies=[Depends(get_principal)])
def trade_signals_skip(signal_id: str) -> dict[str, Any]:
    """Dismiss a pending signal without trading."""
    from forward.wiring.alerts import get_trade_signal_queue

    q = get_trade_signal_queue()
    if not q.skip(signal_id):
        raise HTTPException(404, f"Signal {signal_id} not found")
    return {"skipped": signal_id}
