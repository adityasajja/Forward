"""Versioned API routers (``/api/v1``).

The legacy unversioned routes in :mod:`forward.api.legacy` are untouched. These are the
new surface: each route declares the permission it needs as a dependency, so the
check cannot be forgotten in the body.
"""

from __future__ import annotations

from fastapi import APIRouter, FastAPI

from forward.api.routers import (
    analytics,
    audit,
    auth,
    backtests,
    data_status,
    experiments,
    insights,
    instruments,
    learning,
    market_intel,
    monitor,
    optimization,
    options,
    orders,
    paper,
    portfolio,
    reconciliation,
    regime,
    risk,
    screener,
    signal_explorer,
    strategies,
    ui_config,
    watchlists,
)

ROUTERS: tuple[APIRouter, ...] = (
    auth.router,
    watchlists.router,
    instruments.router,
    audit.router,
    orders.router,
    risk.router,
    regime.router,
    paper.router,
    portfolio.router,
    monitor.router,
    reconciliation.router,
    screener.router,
    backtests.router,
    strategies.router,
    options.router,
    learning.router,
    optimization.router,
    experiments.router,
    market_intel.router,
    signal_explorer.router,
    insights.router,
    # Post-trade attribution. Read-only, and it reads a projection of the journal
    # rather than anything the trading path writes — so including it cannot alter
    # the behaviour of a single order, gate or strategy.
    analytics.router,
    data_status.router,
    ui_config.router,
)



def include_routers(app: FastAPI) -> None:
    for router in ROUTERS:
        app.include_router(router)


__all__ = ["ROUTERS", "include_routers"]
