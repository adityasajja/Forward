"""Market Intelligence routes — ``/api/v1/market-intel``.

Indian cash-equity market-context endpoints. No F&O, no options.

Endpoints
---------
GET /api/v1/market-intel/summary
    Full market overview: NIFTY trend, breadth, A/D ratio, 52w highs/lows,
    regime classification with explicit raw measurements and benchmark provenance.

GET /api/v1/market-intel/sectors
    All sector rankings with optional sort key.

GET /api/v1/market-intel/stocks
    Stock leaders / breakout leaderboard, sorted by relative-strength-vs-NIFTY,
    RVOL, or proximity to 52-week high.

GET /api/v1/market-intel/stock/{symbol}
    Full contextual breakdown for a single cash-equity symbol.

GET /api/v1/market-intel/regime-config
    The currently active regime model version and its thresholds.

GET /api/v1/market-intel/strategy-context
    How a strategy performs under a specific market condition (axis + optional
    bucket label). Delegates to :meth:`LearningService.query_market_context_performance`.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from forward.api.deps import require_permission
from forward.auth.models import Principal
from forward.auth.rbac import Permission
from forward.config.settings import get_settings
from forward.core.errors import InvalidMarketIntelSort, MarketHistoryUnavailable
from forward.wiring.market_intel import get_market_intel_service

logger = logging.getLogger("forward.api.market_intel")

router = APIRouter(prefix="/api/v1/market-intel", tags=["market-intel"])

_READ = require_permission(Permission.STRATEGY_READ)
_CONDITION_AXES = frozenset(
    {
        "market_regime", "regime", "volatility_regime", "stock_rs",
        "stock_rs_bucket", "sector_strength", "sector_strength_bucket",
        "market_breadth", "breadth_bucket", "nifty_trend", "nifty_trend_bucket",
    }
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _abort(status: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"detail": detail})


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/summary")
def market_intel_summary(
    force_refresh: bool = Query(False, description="Bypass TTL cache"),
    principal: Principal = Depends(_READ),
) -> dict[str, Any]:
    """Full market overview.

    Returns:
    - NIFTY 50 trend %, above/below EMA20/EMA50
    - NIFTY 500 breadth: advancing / declining counts + A/D ratio
    - % of universe above EMA20, EMA50, SMA200
    - 52-week highs vs lows count
    - Market volatility (ATR%, volatility ratio)
    - Market trend strength score
    - Sector participation %
    - Regime classification with raw measurements **and** explicit benchmark provenance
    - Top/bottom sectors, top breakouts, top RS stocks
    """
    try:
        svc = get_market_intel_service()
        summary, _sectors, _stocks = svc.compute_all(force_refresh=force_refresh)
        payload = summary.as_dict()
        return {"ok": True, "data": payload}
    except MarketHistoryUnavailable as exc:
        raise _abort(503, str(exc)) from exc
    except Exception as exc:
        logger.exception("market-intel summary error")
        raise _abort(500, f"market intelligence summary failed: {exc}") from exc


@router.get("/live")
def market_intel_live(principal: Principal = Depends(_READ)) -> dict[str, Any]:
    """Live Nifty 50 and India VIX from the broker's index feed.

    The rest of this API reads daily candles that finish at the close. This is the one
    part that moves during the session: the page polls it every few seconds. Outside
    market hours (and on holidays) the feed still answers with the last session's
    values, flagged ``market_open: false``, so the page can say so instead of
    presenting them as live. ``available: false`` means no broker session.
    """
    from forward.api.stream import get_broadcaster
    from forward.market_calendar import is_market_open

    try:
        broadcaster = get_broadcaster()
        available = broadcaster.ensure_indices()
        quotes = broadcaster.latest_indices() if available else {}
    except Exception as exc:  # noqa: BLE001 - the page falls back to the daily numbers
        logger.warning("live index quotes unavailable: %s", exc)
        available, quotes = False, {}

    def pick(name: str) -> dict[str, Any] | None:
        q = quotes.get(name)
        if not q:
            return None
        return {k: q[k] for k in ("ltp", "prev_close", "chg", "chg_pct", "age_s")}

    return {
        "available": available,
        "market_open": bool(is_market_open()),
        "nifty": pick("NIFTY 50"),
        "vix": pick("INDIA VIX"),
    }


@router.get("/sectors")
def market_intel_sectors(
    sort_by: str = Query(
        "relative_strength_1d",
        description=(
            "Sort key: relative_strength_1d | relative_strength_1m | "
            "return_1d_pct | return_1w_pct | return_1m_pct | volume_multiple | "
            "advance_decline_ratio | above_ema50_pct | breakout_count"
        ),
    ),
    descending: bool = Query(True),
    force_refresh: bool = Query(False),
    principal: Principal = Depends(_READ),
) -> dict[str, Any]:
    """All sector rankings.

    Returns every sector ranked by the chosen metric.  The raw values
    (return %, breadth %, relative strength %) are always included so the
    caller can re-sort client-side without a round-trip.
    """
    try:
        svc = get_market_intel_service()
        ranked = svc.ranked_sectors(
            sort_by=sort_by,
            descending=descending,
            force_refresh=force_refresh,
        )
        return {
            "ok": True,
            "sort_by": sort_by,
            "descending": descending,
            "count": len(ranked),
            "sectors": [s.as_dict() for s in ranked],
        }
    except InvalidMarketIntelSort as exc:
        raise _abort(400, str(exc)) from exc
    except MarketHistoryUnavailable as exc:
        raise _abort(503, str(exc)) from exc
    except Exception as exc:
        logger.exception("market-intel sectors error")
        raise _abort(500, f"sector rankings failed: {exc}") from exc


@router.get("/stocks")
def market_intel_stocks(
    sort_by: str = Query(
        "relative_strength_nifty_20d",
        description=(
            "Sort key: relative_strength_nifty_20d | relative_volume | "
            "from_52w_high_pct | atr_pct | trend_pct | change_1d_pct"
        ),
    ),
    descending: bool = Query(True),
    breakouts_only: bool = Query(False, description="Restrict to stocks where is_breakout=True"),
    above_ema50_only: bool = Query(False),
    sector: str | None = Query(None),
    limit: int = Query(
        get_settings().market_intel_default_page_size,
        ge=1,
        le=get_settings().market_intel_max_page_size,
    ),
    force_refresh: bool = Query(False),
    principal: Principal = Depends(_READ),
) -> dict[str, Any]:
    """Stock leaders and breakout leaderboard.

    A live, ranked view of which NSE cash-equity names are exhibiting the
    strongest relative-strength, highest relative volume, or are closest to
    their 52-week highs — with all numeric context fields attached.
    """
    try:
        svc = get_market_intel_service()
        rows = svc.ranked_stocks(
            sort_by=sort_by,
            descending=descending,
            breakouts_only=breakouts_only,
            above_ema50_only=above_ema50_only,
            sector=sector,
            limit=limit,
            force_refresh=force_refresh,
        )
        return {
            "ok": True,
            "sort_by": sort_by,
            "filters": {
                "breakouts_only": breakouts_only,
                "above_ema50_only": above_ema50_only,
                "sector": sector,
            },
            "count": len(rows),
            "stocks": [s.as_dict() for s in rows],
        }
    except InvalidMarketIntelSort as exc:
        raise _abort(400, str(exc)) from exc
    except MarketHistoryUnavailable as exc:
        raise _abort(503, str(exc)) from exc
    except Exception as exc:
        logger.exception("market-intel stocks error")
        raise _abort(500, f"stock leaderboard failed: {exc}") from exc


@router.get("/stock/{symbol}")
def market_intel_stock(
    symbol: str,
    force_refresh: bool = Query(False),
    principal: Principal = Depends(_READ),
) -> dict[str, Any]:
    """Full contextual breakdown for one cash-equity symbol.

    Returns all ``StockContext`` fields: trend, RS vs NIFTY (20d), RVOL,
    ATR%, 52w high/low distances, gap, sector, sector RS, market breadth,
    and the benchmark provenance used for all relative calculations.
    """
    try:
        svc = get_market_intel_service()
        ctx = svc.stock_context(symbol, force_refresh=force_refresh)
        if ctx is None:
            raise _abort(404, f"symbol '{symbol}' not found in cached universe")

        return {"ok": True, "data": ctx.as_dict()}
    except HTTPException:
        raise
    except MarketHistoryUnavailable as exc:
        raise _abort(503, str(exc)) from exc
    except Exception as exc:
        logger.exception("market-intel stock/{} error", symbol)
        raise _abort(500, f"stock context failed: {exc}") from exc


@router.get("/regime-config")
def market_intel_regime_config(
    principal: Principal = Depends(_READ),
) -> dict[str, Any]:
    """The active regime model version and all configurable thresholds.

    Every market regime classification is tagged with this version so a
    historical observation can be replayed under the model that produced it.
    """
    try:
        svc = get_market_intel_service()
        return {"ok": True, "regime_config": svc.regime_config_payload()}
    except Exception as exc:
        logger.exception("market-intel regime-config error")
        raise _abort(500, f"regime config read failed: {exc}") from exc


@router.get("/strategy-context")
def market_intel_strategy_context(
    strategy: str | None = Query(None, description="strategy_key or strategy_id to filter"),
    condition_axis: str = Query(
        ...,
        description=(
            "Market-context axis to slice on: market_regime | nifty_trend_bucket | "
            "breadth_bucket | sector_strength_bucket | stock_rs_bucket | volatility_regime"
        ),
    ),
    condition_value: str | None = Query(
        None,
        description="Specific bucket label to query (omit to return all buckets)",
    ),
    grades: str | None = Query(
        None,
        description="Comma-separated evidence grades to include: forward,in_sample (default: both)",
    ),
    metric: str | None = Query(None, description="Outcome metric: net_pnl or return_pct"),
    min_sample: int = Query(
        10,
        ge=1,
        description="Minimum trades per bucket to publish a finding",
    ),
    principal: Principal = Depends(_READ),
) -> dict[str, Any]:
    """Strategy performance conditioned on a market context.

    Answers questions such as:
    - *How does [Strategy] perform when NIFTY is in uptrend vs sideways?*
    - *Does this breakout setup work better when the stock is stronger than NIFTY?*
    - *How does performance differ in strong vs weak sectors?*

    Each returned bucket carries: ``n``, ``n_forward``, ``mean``, ``median``,
    ``win_rate``, ``profit_factor``, ``ci_low/ci_high``, and an
    ``evidence_note`` (``forward`` / ``in_sample_only`` /
    ``insufficient_forward_observations``) so the caller never confuses a
    backtest artefact for out-of-sample evidence.
    """
    if condition_axis not in _CONDITION_AXES:
        raise _abort(
            400,
            f"condition_axis must be one of: {', '.join(sorted(_CONDITION_AXES))}",
        )

    grade_list = [g.strip() for g in grades.split(",")] if grades else None

    try:
        from forward.composition import get_learning_service

        result = get_learning_service().query_market_context_performance(
            strategy=strategy,
            condition_axis=condition_axis,
            condition_value=condition_value,
            grades=grade_list,
            metric=metric,
            min_sample=min_sample,
        )
        return {"ok": True, **result}
    except Exception as exc:
        logger.exception("market-intel strategy-context error")
        raise _abort(500, f"strategy context query failed: {exc}") from exc
