"""Post-trade analytics routes — ``/api/v1/analytics``.

The read surface over the attribution layer: one closed trade explained across
nine branches, and the aggregates across a whole book.

Every route here is **read-only**, and that is a property this module asserts
rather than merely intends. There is no write endpoint, no re-attribution
trigger reachable from a route, and no route that can change a threshold
persistently — attribution is performed by the service on its own schedule, not
by a caller. ``tests/test_attribution_api.py`` enumerates the router's mutating
method set and fails the build if it ever becomes non-empty, the same guard the
learning router carries and for the same reason.

The scope rule
--------------

Every aggregate takes the same filters, and every aggregate reports the evidence
grades of the rows it was computed over. A caller may scope to ``forward``
explicitly; if they do not, a mixed book is summarised as a mixed book, with the
counts shown. What the surface will not do is silently blend the two and let a
mean over backfilled trades read as a forward finding.

There is deliberately no "best strategy" endpoint. ``/performance/by-*`` compares
a strategy across regimes, contexts, sectors, sizing methods and exits; the
comparison is the product and the verdict is withheld.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from forward.api.deps import require_permission
from forward.auth.models import Principal
from forward.auth.rbac import Permission
from forward.config.settings import get_settings
from forward.core.errors import AnalyticsBookTooLarge, InvalidAnalyticsFilter

logger = logging.getLogger("forward.api.analytics")

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])

_READ = require_permission(Permission.STRATEGY_READ)


def _get_service() -> Any:
    from forward.composition import get_analytics_service

    return get_analytics_service()


def _abort(status: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"detail": detail})


def _report(method: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run an analytics report and map its query errors to HTTP responses."""
    try:
        return method(*args, **kwargs)
    except InvalidAnalyticsFilter as exc:
        raise _abort(400, str(exc)) from exc
    except AnalyticsBookTooLarge as exc:
        raise _abort(413, str(exc)) from exc


# ---------------------------------------------------------------------------
# trades
# ---------------------------------------------------------------------------


@router.get("/trades")
def list_attributed_trades(
    limit: int = Query(get_settings().page_sizes["analytics_default"], ge=1, le=get_settings().page_sizes["analytics_max"]),
    offset: int = Query(0, ge=0),
    strategy_id: str | None = Query(None),
    strategy_version: int | None = Query(None, ge=1),
    symbol: str | None = Query(None),
    source: str | None = Query(None, description="BACKTEST | PAPER | LIVE"),
    provenance: str | None = Query(None, description="forward | in_sample — the evidence grade"),
    market_regime: str | None = Query(None),
    sector: str | None = Query(None),
    start: datetime | None = Query(None, description="computed_at lower bound (ISO)"),
    end: datetime | None = Query(None, description="computed_at upper bound (ISO)"),
    reason_code: str | None = Query(None, description="a code from the vocabulary"),
    principal: Principal = Depends(_READ),
) -> dict[str, Any]:
    """Attributed trades, newest first, with the headline summary of the page.

    The response carries ``items`` *and* the aggregate over everything the filters
    matched, because a reader arriving at this screen wants both a list and a
    sense of the book — and a page of twenty rows with no context is a list of
    twenty rows.
    """
    try:
        rows, total, filters = _get_service().trade_page(
            principal.user_id,
            limit=limit,
            offset=offset,
            reason_code=reason_code,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            symbol=symbol,
            source=source,
            provenance=provenance,
            market_regime=market_regime,
            sector=sector,
            start=start,
            end=end,
        )
    except InvalidAnalyticsFilter as exc:
        raise _abort(400, str(exc)) from exc
    except AnalyticsBookTooLarge as exc:
        raise _abort(413, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("analytics trades list failed")
        raise _abort(500, f"trades list failed: {exc}") from exc

    return {
        "ok": True,
        "data": {
            "items": [_summary_of(row) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
            "filters": {k: _jsonable(v) for k, v in filters.items() if v is not None},
        },
    }


def _jsonable(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _summary_of(row: dict[str, Any]) -> dict[str, Any]:
    """The list-view shape of one attribution: enough to render a row, and the
    ids needed to open the detail.

    ``entry_ts`` and ``exit_ts`` come from the stored tree rather than from the
    columns: they are not lifted into the table, because no axis slices on them
    and the ordered read already sorts by ``computed_at``. A reader who wants them
    per row gets them here.
    """
    tree = row.get("attribution")
    if not isinstance(tree, dict):
        raise ValueError("stored attribution is missing its attribution object")
    outcome = tree.get("outcome")
    thresholds = tree.get("thresholds")
    reason_codes = row.get("reason_codes")
    if not isinstance(outcome, dict) or not isinstance(thresholds, dict):
        raise ValueError("stored attribution is missing outcome or thresholds")
    if not isinstance(reason_codes, list) or not all(
        isinstance(code, str) for code in reason_codes
    ):
        raise ValueError("stored attribution reason_codes must be a list of strings")
    return {
        "trade_id": row.get("trade_id"),
        "symbol": row.get("symbol"),
        "side": row.get("side"),
        "source": row.get("source"),
        "evidence_grade": row.get("evidence_grade"),
        "evidence_class": row.get("evidence_class"),
        "simulated": row.get("simulated"),
        "holding_sec": row.get("holding_sec"),
        "exit_reason": row.get("exit_reason"),
        "net_pnl": outcome.get("net_pnl"),
        "net_return_pct": outcome.get("net_return_pct"),
        "gross_pnl": outcome.get("gross_pnl"),
        "mfe_pct": row.get("mfe_pct"),
        "mae_pct": row.get("mae_pct"),
        "mfe_over_risk": row.get("mfe_over_risk"),
        "realized_over_risk": row.get("realized_over_risk"),
        "capture_efficiency_pct": row.get("capture_efficiency_pct"),
        "entry_slippage_bps": row.get("entry_slippage_bps"),
        "exit_slippage_bps": row.get("exit_slippage_bps"),
        "total_slippage_bps": row.get("total_slippage_bps"),
        "transaction_costs": row.get("transaction_costs"),
        "context_score": row.get("context_score"),
        "context_class": row.get("context_class"),
        "market_regime": row.get("market_regime"),
        "sector": row.get("sector"),
        "sizing_method": row.get("sizing_method"),
        "realized_risk_pct": row.get("realized_risk_pct"),
        "partial_fill": row.get("partial_fill"),
        "fill_ratio": row.get("fill_ratio"),
        "entry_quality": row.get("entry_quality"),
        "execution_quality": row.get("execution_quality"),
        "thresholds": thresholds,
        "slippage_high_bps": thresholds.get("slippage_high_bps"),
        "reason_codes": reason_codes,
        "computed_at": _jsonable(row.get("computed_at")),
    }


@router.get("/trades/{trade_id}/attribution")
def trade_attribution(
    trade_id: str,
    principal: Principal = Depends(_READ),
) -> dict[str, Any]:
    """One closed trade, explained across the nine branches.

    The whole object: the TRADE tree, the excursion set with its methodology
    fields, every reason code with the measurement and threshold that produced
    it, and the inputs that could not be resolved. A 404 for a trade that does not
    exist *or* that belongs to someone else — deliberately the same answer, so the
    route cannot be used to discover which trades exist.
    """
    try:
        row = _get_service().trade_attribution(principal.user_id, trade_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("analytics attribution detail failed")
        raise _abort(500, f"attribution read failed: {exc}") from exc
    if row is None:
        raise _abort(404, f"no attribution for trade '{trade_id}'")
    return {
        "ok": True,
        "data": {
            **row,
            "computed_at": _jsonable(row.get("computed_at")),
            "updated_at": _jsonable(row.get("updated_at")),
        },
    }


# ---------------------------------------------------------------------------
# aggregates
# ---------------------------------------------------------------------------


@router.get("/summary")
def analytics_summary(
    strategy_id: str | None = Query(None),
    strategy_version: int | None = Query(None, ge=1),
    symbol: str | None = Query(None),
    source: str | None = Query(None, description="BACKTEST | PAPER | LIVE"),
    provenance: str | None = Query(None, description="forward | in_sample"),
    market_regime: str | None = Query(None),
    sector: str | None = Query(None),
    principal: Principal = Depends(_READ),
) -> dict[str, Any]:
    """Headline performance, with the evidence composition stated.

    ``counts`` always accompanies the figures: a mean computed over forty
    in-sample trades and two forward ones is a statement about the in-sample
    book, and a reader who cannot see that will read it as a finding.
    """
    try:
        data = _report(
            _get_service().summary_report,
            principal.user_id,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            symbol=symbol,
            source=source,
            provenance=provenance,
            market_regime=market_regime,
            sector=sector,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("analytics summary failed")
        raise _abort(500, f"summary failed: {exc}") from exc
    return {"ok": True, "data": data}


@router.get("/performance/by-strategy")
def performance_by_strategy(
    principal: Principal = Depends(_READ),
    strategy_id: str | None = Query(None),
    source: str | None = Query(None),
    provenance: str | None = Query(None),
    market_regime: str | None = Query(None),
) -> dict[str, Any]:
    """Performance and attribution quality, per strategy version."""
    data = _report(
        _get_service().strategy_report,
        principal.user_id,
        strategy_id=strategy_id,
        source=source,
        provenance=provenance,
        market_regime=market_regime,
    )
    return {"ok": True, "data": data}


@router.get("/performance/by-regime")
def performance_by_regime(
    principal: Principal = Depends(_READ),
    strategy_id: str | None = Query(None),
    source: str | None = Query(None),
    provenance: str | None = Query(None),
    sector: str | None = Query(None),
) -> dict[str, Any]:
    """Performance cut by the market regime recorded at entry."""
    data = _report(
        _get_service().regime_report,
        principal.user_id,
        strategy_id=strategy_id,
        source=source,
        provenance=provenance,
        sector=sector,
    )
    return {"ok": True, "data": data}


@router.get("/performance/by-context")
def performance_by_context(
    principal: Principal = Depends(_READ),
    strategy_id: str | None = Query(None),
    source: str | None = Query(None),
    provenance: str | None = Query(None),
) -> dict[str, Any]:
    """Performance cut by the recorded signal-context class and score band.

    The context is the *recorded* one — the classification the signal engine made
    at signal time, under a stated model version. It is not recomputed here, so
    this cannot silently restate history under a newer model.
    """
    data = _report(
        _get_service().context_report,
        principal.user_id,
        strategy_id=strategy_id,
        source=source,
        provenance=provenance,
    )
    return {"ok": True, "data": data}


@router.get("/performance/by-sizing")
def performance_by_sizing(
    principal: Principal = Depends(_READ),
    strategy_id: str | None = Query(None),
    source: str | None = Query(None),
    provenance: str | None = Query(None),
) -> dict[str, Any]:
    """Performance cut by sizing method and by whether a cap bound the size."""
    data = _report(
        _get_service().sizing_report,
        principal.user_id,
        strategy_id=strategy_id,
        source=source,
        provenance=provenance,
    )
    return {"ok": True, "data": data}


@router.get("/performance/by-execution")
def performance_by_execution(
    principal: Principal = Depends(_READ),
    strategy_id: str | None = Query(None),
    source: str | None = Query(None),
    provenance: str | None = Query(None),
) -> dict[str, Any]:
    """Performance cut by execution quality, plus the slippage distribution.

    This is the route that answers the requirement's stated question directly:
    *does a profitable strategy with poor execution read differently from a
    strong execution of a weak signal?* It reads differently here because
    execution quality is its own dimension, never folded into a single score.
    """
    data = _report(
        _get_service().execution_report,
        principal.user_id,
        strategy_id=strategy_id,
        source=source,
        provenance=provenance,
    )
    return {"ok": True, "data": data}


@router.get("/mae-mfe")
def mae_mfe_endpoint(
    principal: Principal = Depends(_READ),
    strategy_id: str | None = Query(None),
    source: str | None = Query(None),
    provenance: str | None = Query(None),
    symbol: str | None = Query(None),
    include_points: bool = Query(
        True,
        description=(
            "Include per-trade points. Off for a large book where only the summary is wanted."
        ),
    ),
) -> dict[str, Any]:
    """MAE/MFE distributions and their relationship to the final P&L.

    ``methodology`` states how the figures were produced, because an excursion is
    only interpretable alongside the observation window it was measured over: MAE
    and MFE are measured on the bars between entry and exit, from each bar's high
    and low, truncated at the exit. A reader who assumed a tick-level or a
    close-only measurement would read the numbers wrong.
    """
    data = _report(
        _get_service().mae_mfe_report,
        principal.user_id,
        include_points=include_points,
        strategy_id=strategy_id,
        source=source,
        provenance=provenance,
        symbol=symbol,
    )
    return {"ok": True, "data": data}


__all__ = ["router"]
