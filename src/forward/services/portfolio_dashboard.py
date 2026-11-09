"""Project a portfolio book into dashboard policy views."""

from __future__ import annotations

from typing import Any

from forward.services.portfolio_models import PortfolioBook
from forward.services.portfolio_policy import PortfolioPolicy


def top_exposures(
    values: dict[str, float], capital: float, count: int
) -> list[dict[str, Any]]:
    """Return the largest exposures with their share of allocated capital.

    Args:
        values: Exposure amount for each name.
        capital: Total allocated capital.
        count: Maximum number of rows to return.

    Returns:
        Exposure rows sorted by absolute value.
    """
    ranked = sorted(values.items(), key=lambda item: abs(item[1]), reverse=True)[: max(count, 0)]
    return [
        {
            "name": name,
            "value": round(value, 2),
            "pct": round(value / capital, 4) if capital else None,
        }
        for name, value in ranked
    ]


def concentration_warnings(
    val_by_symbol: dict[str, float],
    sectors: dict[str, dict[str, Any]],
    strat_exposure: dict[str, float],
    capital: float,
    policy: PortfolioPolicy,
) -> list[str]:
    """Describe configured exposure limits close to their warning threshold.

    Args:
        val_by_symbol: Net value held in each symbol.
        sectors: Sector totals with their ``value`` amounts.
        strat_exposure: Gross exposure for each strategy.
        capital: Total allocated capital.
        policy: Portfolio limits and warning threshold.

    Returns:
        Warnings for configured limits near their thresholds.
    """
    warnings: list[str] = []
    if capital <= 0:
        return warnings
    bar = policy.warn_at_pct_of_limit

    def warn(label: str, value: float, limit: float | None) -> None:
        if limit is None or limit <= 0:
            return
        utilization = value / limit
        if utilization >= bar:
            warnings.append(
                f"{label} at {utilization:.0%} of its limit ({value:,.0f} of {limit:,.0f})"
            )

    for symbol, value in val_by_symbol.items():
        warn(f"{symbol} exposure", abs(value), policy.max_stock_exposure)
    for sector, entry in sectors.items():
        limit = (
            policy.max_sector_exposure_pct * capital
            if policy.max_sector_exposure_pct is not None
            else None
        )
        warn(f"{sector} sector exposure", abs(float(entry.get("value") or 0.0)), limit)
    for strategy, value in strat_exposure.items():
        warn(f"strategy {strategy} exposure", abs(value), policy.max_total_exposure)
    warn(
        "portfolio exposure",
        sum(abs(value) for value in val_by_symbol.values()),
        policy.max_total_exposure,
    )
    return warnings


def describe_conflicts(book: PortfolioBook) -> list[dict[str, Any]]:
    """List symbols held in opposite directions across deployments.

    Args:
        book: Aggregated portfolio book.

    Returns:
        Conflicting long and short deployments by symbol.
    """
    by_symbol: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for deployment in book.deployments:
        for position in deployment.positions.values():
            if abs(position.qty) <= 1e-9:
                continue
            sides = by_symbol.setdefault(position.symbol, {"long": [], "short": []})
            sides["long" if position.qty > 0 else "short"].append(
                {
                    "deployment_id": deployment.deployment_id,
                    "strategy_id": deployment.strategy_id,
                    "qty": round(position.qty, 4),
                }
            )
    conflicts = []
    for symbol in sorted(by_symbol):
        sides = by_symbol[symbol]
        if sides["long"] and sides["short"]:
            conflicts.append(
                {
                    "symbol": symbol,
                    "net_qty": round(
                        sum(row["qty"] for row in sides["long"])
                        - sum(row["qty"] for row in sides["short"]),
                        4,
                    ),
                    "long": sides["long"],
                    "short": sides["short"],
                }
            )
    return conflicts


def active_limits(
    book: PortfolioBook, policy: PortfolioPolicy, capital: float, gross: float
) -> list[dict[str, Any]]:
    """Show configured portfolio limits beside their current readings.

    Args:
        book: Aggregated portfolio book.
        policy: Configured portfolio limits.
        capital: Total allocated capital.
        gross: Current gross portfolio exposure.

    Returns:
        The active limits and their utilization values.
    """

    def row(key: str, label: str, value: Any, current: Any, unit: str) -> dict[str, Any]:
        utilization = None
        if isinstance(value, (int, float)) and isinstance(current, (int, float)) and value:
            utilization = round(current / value, 4)
        return {
            "key": key,
            "label": label,
            "configured": value,
            "current": current,
            "unit": unit,
            "utilization": utilization,
        }

    rows = [
        row(
            "max_total_exposure",
            "Total portfolio exposure",
            policy.max_total_exposure,
            round(gross, 2),
            "INR",
        ),
        row("max_daily_loss", "Daily portfolio loss", policy.max_daily_loss, None, "INR"),
        row(
            "max_capital_per_strategy",
            "Maximum capital per strategy",
            policy.max_capital_per_strategy,
            None,
            "INR",
        ),
        row(
            "max_open_positions",
            "Maximum open positions",
            policy.max_open_positions,
            len(book.open_symbols()),
            "count",
        ),
        row(
            "max_stock_exposure",
            "Maximum exposure per stock",
            policy.max_stock_exposure,
            None,
            "INR",
        ),
        row(
            "max_sector_exposure_pct",
            "Maximum sector exposure",
            policy.max_sector_exposure_pct,
            None,
            "fraction",
        ),
        row(
            "max_correlated_exposure_pct",
            "Maximum correlated exposure",
            policy.max_correlated_exposure_pct,
            None,
            "fraction",
        ),
    ]
    rows.append(
        {
            "key": "conflict_mode",
            "label": "Opposite-direction rule",
            "configured": policy.conflict_mode,
            "current": len(describe_conflicts(book)),
            "unit": "mode",
            "utilization": None,
        }
    )
    return rows
