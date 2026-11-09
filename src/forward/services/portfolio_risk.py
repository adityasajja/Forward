"""Portfolio order checks and the order-gate factory."""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from forward.core.portfolio_ports import PortfolioStoragePort
from forward.services.portfolio import PortfolioService
from forward.services.portfolio_models import PortfolioBook, _clean, _side_sign, sector_resolver
from forward.services.portfolio_policy import PortfolioPolicy

logger = logging.getLogger("forward.services.portfolio_risk")


@dataclass(frozen=True)
class PortfolioCheck:
    check: str
    allowed: bool
    detail: str
    limit: Any = None
    current: Any = None
    projected: Any = None


@dataclass(frozen=True)
class PortfolioDecision:
    allowed: bool
    reason: str | None
    code: str | None
    checks: tuple[PortfolioCheck, ...] = ()

    @classmethod
    def allow(cls, checks: list[PortfolioCheck], *, note: str | None = None) -> PortfolioDecision:
        return cls(True, note, None, tuple(checks))

    @classmethod
    def reject(cls, reason: str, code: str, checks: list[PortfolioCheck]) -> PortfolioDecision:
        return cls(False, reason, code, tuple(checks))


def resolve_price(draft: Any, book: PortfolioBook) -> float | None:
    """A price for the incoming order's notional, or ``None`` when unpriceable.

    Limit/requested first, then the book's last mark for the symbol. ``None``
    does not mean zero — callers skip the notional checks and say so, exactly
    like the strategy gate's unpriceable backstop.
    """
    for candidate in (
        getattr(draft, "limit_price", None),
        getattr(draft, "requested_price", None),
    ):
        try:
            if candidate is not None and float(candidate) > 0:
                return float(candidate)
        except TypeError, ValueError:
            continue
    wanted = _clean(getattr(draft, "symbol", ""))
    for dep in book.deployments:
        position = dep.positions.get(wanted)
        if position is not None and position.last_price > 0:
            return position.last_price
    return None


def evaluate_order(
    draft: Any,
    *,
    book: PortfolioBook,
    policy: PortfolioPolicy,
    price: float | None,
    daily_pnl: float = 0.0,
    sector_of: Any = None,
) -> PortfolioDecision:
    """May this order reach the OMS? Pure: book + policy + price in, verdict out.

    ``draft`` needs ``symbol``, ``side``, ``quantity``, ``deployment_id`` and
    ``strategy_id``. ``sector_of`` maps a symbol to its sector (``"Unknown"``
    when unmapped); without it every symbol is its own one-member cluster.
    """
    checks: list[PortfolioCheck] = []
    symbol = _clean(getattr(draft, "symbol", ""))
    side = _side_sign(getattr(draft, "side", None))
    try:
        qty = float(getattr(draft, "quantity", 0.0) or 0.0)
    except TypeError, ValueError:
        return PortfolioDecision.reject(
            "order quantity is not a number", "portfolio_bad_order", checks
        )
    if qty <= 0:
        return PortfolioDecision.reject(
            "order quantity is not positive", "portfolio_bad_order", checks
        )
    if not math.isfinite(qty):
        return PortfolioDecision.reject(
            "order quantity must be finite", "portfolio_bad_order", checks
        )
    if price is not None and (not math.isfinite(price) or price <= 0):
        return PortfolioDecision.reject(
            "order price must be finite and positive", "portfolio_bad_order", checks
        )

    arm_qty = book.arm_qty(getattr(draft, "deployment_id", None), symbol)
    if arm_qty != 0 and (arm_qty > 0) != (side > 0) and qty <= abs(arm_qty):
        # An exit (or partial exit): blocking it would lock a loss in place.
        # The strategy gate already passed; the portfolio layer steps aside.
        checks.append(
            PortfolioCheck(
                "exit_exempt",
                True,
                f"reduces the arm's own {abs(arm_qty):g} {symbol} position",
            )
        )
        return PortfolioDecision.allow(checks, note="exit exempt from portfolio limits")

    if not symbol:
        return PortfolioDecision.reject("order has no symbol", "portfolio_bad_order", checks)

    notional = qty * price if price is not None else None
    net = book.net_qty(symbol)
    conflict = net != 0 and (net > 0) != (side > 0)

    if conflict:
        holders = sorted(
            {
                dep.strategy_id or dep.deployment_id
                for dep in book.deployments
                for position in dep.positions.values()
                if position.symbol == symbol
                and position.qty != 0
                and (position.qty > 0) != (side > 0)
            }
        )
        detail = f"{symbol} is held {abs(net):g} opposite by {', '.join(holders) or 'another arm'}"
        if policy.conflict_mode == "reject":
            checks.append(PortfolioCheck("conflict", False, detail))
            return PortfolioDecision.reject(
                f"conflicting order rejected ({detail}; mode=reject)",
                "portfolio_conflict",
                checks,
            )
        if policy.conflict_mode == "priority":
            mine = _rank(policy, getattr(draft, "strategy_id", None))
            theirs = max([_rank(policy, holder) for holder in holders] or [0.0])
            if mine > theirs:
                checks.append(
                    PortfolioCheck(
                        "conflict",
                        True,
                        f"{detail}; strategy rank {mine:g} outranks {theirs:g}",
                    )
                )
            else:
                checks.append(
                    PortfolioCheck(
                        "conflict",
                        False,
                        f"{detail}; strategy rank {mine:g} does not outrank {theirs:g}",
                    )
                )
                return PortfolioDecision.reject(
                    f"conflicting order rejected ({detail}; mode=priority)",
                    "portfolio_conflict_priority",
                    checks,
                )
        else:  # "net" — both books stand; gross size limits still apply below.
            checks.append(PortfolioCheck("conflict", True, f"{detail}; netted (mode=net)"))
    else:
        checks.append(
            PortfolioCheck(
                "conflict",
                True,
                f"no opposite {symbol} holding across the book; exposure aggregates",
            )
        )

    sector_fn = sector_of or (lambda s: "Unknown")
    capital = book.total_capital
    gross = book.gross_exposure()

    def _deny(check: str, reason: str, code: str, **kw: Any) -> PortfolioDecision:
        checks.append(PortfolioCheck(check, False, reason, **kw))
        return PortfolioDecision.reject(reason, code, checks)

    def _pass(check: str, detail: str, **kw: Any) -> None:
        checks.append(PortfolioCheck(check, True, detail, **kw))

    if policy.max_total_exposure is not None:
        if notional is None:
            _pass(
                "total_exposure",
                "order cannot be priced; notional check skipped, counts still apply",
                limit=policy.max_total_exposure,
                current=round(gross, 2),
            )
        elif gross + notional > policy.max_total_exposure:
            return _deny(
                "total_exposure",
                f"portfolio exposure {gross + notional:,.0f} would exceed "
                f"{policy.max_total_exposure:,.0f}",
                "portfolio_total_exposure",
                limit=policy.max_total_exposure,
                current=round(gross, 2),
                projected=round(gross + notional, 2),
            )
        else:
            _pass(
                "total_exposure",
                f"{gross + notional:,.0f} of {policy.max_total_exposure:,.0f}",
                limit=policy.max_total_exposure,
                current=round(gross, 2),
                projected=round(gross + notional, 2),
            )

    if policy.max_stock_exposure is not None:
        current_stock = abs(book.symbol_value(symbol))
        if notional is None:
            _pass(
                "stock_exposure",
                "order cannot be priced; stock check skipped",
                limit=policy.max_stock_exposure,
                current=round(current_stock, 2),
            )
        elif current_stock + notional > policy.max_stock_exposure:
            return _deny(
                "stock_exposure",
                f"{symbol} exposure {current_stock + notional:,.0f} would exceed "
                f"{policy.max_stock_exposure:,.0f}",
                "portfolio_stock_exposure",
                limit=policy.max_stock_exposure,
                current=round(current_stock, 2),
                projected=round(current_stock + notional, 2),
            )
        else:
            _pass(
                "stock_exposure",
                f"{symbol} {current_stock + notional:,.0f} of {policy.max_stock_exposure:,.0f}",
                limit=policy.max_stock_exposure,
                current=round(current_stock, 2),
                projected=round(current_stock + notional, 2),
            )

    if policy.max_sector_exposure_pct is not None:
        if capital <= 0:
            _pass(
                "sector_exposure",
                "no allocated capital; percentage check skipped",
                limit=policy.max_sector_exposure_pct,
            )
        elif notional is None:
            _pass(
                "sector_exposure",
                "order cannot be priced; sector check skipped",
                limit=policy.max_sector_exposure_pct,
            )
        else:
            sector = sector_fn(symbol)
            # Gross basis: a sector holding +100 long against −100 short is
            # 200 of exposure, not zero. Netting across arms for limit
            # purposes would let offsetting books hide concentration.
            sector_value = sum(
                abs(p.value)
                for dep in book.deployments
                for p in dep.positions.values()
                if sector_fn(p.symbol) == sector
            )
            add = notional if sector_fn(symbol) == sector else 0.0
            projected = (sector_value + add) / capital
            if projected > policy.max_sector_exposure_pct:
                return _deny(
                    "sector_exposure",
                    f"{sector} exposure {projected:.1%} would exceed "
                    f"{policy.max_sector_exposure_pct:.1%} of capital",
                    "portfolio_sector_exposure",
                    limit=policy.max_sector_exposure_pct,
                    current=round(sector_value / capital, 4),
                    projected=round(projected, 4),
                )
            _pass(
                "sector_exposure",
                f"{sector} {projected:.1%} of {policy.max_sector_exposure_pct:.1%}",
                limit=policy.max_sector_exposure_pct,
                projected=round(projected, 4),
            )

    if policy.max_correlated_exposure_pct is not None:
        if capital <= 0:
            _pass(
                "correlated_exposure",
                "no allocated capital; percentage check skipped",
                limit=policy.max_correlated_exposure_pct,
            )
        elif notional is None:
            _pass(
                "correlated_exposure",
                "order cannot be priced; correlated check skipped",
                limit=policy.max_correlated_exposure_pct,
            )
        else:
            cluster, members = _cluster_for(symbol, book, policy, sector_fn)
            cluster_value = sum(abs(book.symbol_value(member)) for member in members)
            projected = (cluster_value + notional) / capital
            if projected > policy.max_correlated_exposure_pct:
                return _deny(
                    "correlated_exposure",
                    f"correlated cluster ({cluster}) exposure {projected:.1%} "
                    f"would exceed {policy.max_correlated_exposure_pct:.1%} of capital",
                    "portfolio_correlated_exposure",
                    limit=policy.max_correlated_exposure_pct,
                    current=round(cluster_value / capital, 4),
                    projected=round(projected, 4),
                )
            _pass(
                "correlated_exposure",
                f"cluster ({cluster}) {projected:.1%} of {policy.max_correlated_exposure_pct:.1%}",
                limit=policy.max_correlated_exposure_pct,
                projected=round(projected, 4),
            )

    if policy.max_open_positions is not None:
        seen: dict[str, float] = {}
        for dep in book.deployments:
            for position in dep.positions.values():
                seen[position.symbol] = seen.get(position.symbol, 0.0) + position.qty
        seen[symbol] = seen.get(symbol, 0.0) + side * qty
        projected_count = sum(1 for q in seen.values() if abs(q) > 1e-9)
        if projected_count > policy.max_open_positions:
            return _deny(
                "open_positions",
                f"{projected_count} open positions would exceed {policy.max_open_positions}",
                "portfolio_open_positions",
                limit=policy.max_open_positions,
                projected=projected_count,
            )
        _pass(
            "open_positions",
            f"{projected_count} of {policy.max_open_positions}",
            limit=policy.max_open_positions,
            projected=projected_count,
        )

    if policy.max_daily_loss is not None:
        if daily_pnl <= -abs(policy.max_daily_loss):
            return _deny(
                "daily_loss",
                f"portfolio is down {abs(daily_pnl):,.0f} today (limit "
                f"{policy.max_daily_loss:,.0f}); new risk refused, exits still pass",
                "portfolio_daily_loss",
                limit=policy.max_daily_loss,
                current=round(daily_pnl, 2),
            )
        _pass(
            "daily_loss",
            f"today {daily_pnl:+,.0f} against a {policy.max_daily_loss:,.0f} limit",
            limit=policy.max_daily_loss,
            current=round(daily_pnl, 2),
        )

    return PortfolioDecision.allow(checks)


def _rank(policy: PortfolioPolicy, strategy_id: Any) -> float:
    try:
        return float(policy.strategy_priorities.get(str(strategy_id), 0.0))
    except TypeError, ValueError, AttributeError:
        return 0.0


def _cluster_for(
    symbol: str, book: PortfolioBook, policy: PortfolioPolicy, sector_fn: Any
) -> tuple[str, list[str]]:
    """The correlated cluster a symbol belongs to, and its members.

    An explicitly declared ``correlation_groups`` basket wins — the operator
    naming two symbols correlated (index twins, ADR pairs) is a statement no
    price series needs to confirm. Otherwise the cluster is the symbol's
    sector: a documented proxy, not a return-correlation model, which this
    layer deliberately does not build.
    """
    wanted = _clean(symbol)
    for group in policy.correlation_groups:
        members = [_clean(s) for s in group]
        if wanted in members:
            return f"group:{'+'.join(sorted(set(members)))}", sorted(set(members))
    sector = sector_fn(wanted)
    members = sorted(
        {
            p.symbol
            for dep in book.deployments
            for p in dep.positions.values()
            if sector_fn(p.symbol) == sector
        }
        | {wanted}
    )
    return f"sector:{sector}", members


def portfolio_gate_for(
    user_id: str,
    *,
    storage: PortfolioStoragePort,
    sector_table: Mapping[str, str],
    prices: dict[str, float] | None = None,
    order_portfolio: Any = None,
) -> Any:
    """Build a portfolio gate that also checks fee completeness.

    The callable folds a fresh book on every call. Without a stored policy, it
    refuses orders that add exposure because no account risk limits were
    explicitly configured. Orders that reduce exposure remain allowed.

    A gate that cannot read the book fails *closed*: a safety layer that
    silently vanishes on a store fault is the footgun this codebase refuses
    to build.
    """
    from forward.execution.oms import RiskDecision

    service = PortfolioService(storage=storage)
    policy = service.get_policy(user_id)

    def _gate(draft: Any) -> RiskDecision:
        try:
            book = service.build_book(user_id, prices=prices)
            symbol = _clean(getattr(draft, "symbol", ""))
            side = _side_sign(getattr(draft, "side", None))
            quantity = float(getattr(draft, "quantity", 0.0))
            deployment_id = getattr(draft, "deployment_id", None)
            if deployment_id:
                held = book.arm_qty(deployment_id, symbol)
            elif order_portfolio is not None:
                held = float(order_portfolio.position(symbol).quantity)
            else:
                held = book.net_qty(symbol)
            reduces_exposure = abs(held + side * quantity) < abs(held) - 1e-9
            if not book.commission_complete and not reduces_exposure:
                return RiskDecision.reject(
                    "fees are unknown; new exposure is blocked until fee records are complete",
                    code="commission_incomplete",
                )
            if reduces_exposure:
                return RiskDecision.ok()
            if policy is None:
                return RiskDecision.reject(
                    "portfolio risk policy is not configured; new exposure is blocked",
                    code="portfolio_policy_missing",
                )
            price = resolve_price(draft, book)
            daily = service.realized_today(user_id) + _unrealized_now(book)
            decision = evaluate_order(
                draft,
                book=book,
                policy=policy,
                price=price,
                daily_pnl=daily,
                sector_of=sector_resolver(sector_table),
            )
        except Exception as exc:  # noqa: BLE001 — fail closed, loudly
            logger.exception("portfolio gate unreadable; refusing order")
            return RiskDecision.reject(
                f"portfolio risk could not be evaluated ({exc}); refusing rather "
                "than trading unmeasured",
                code="portfolio_unavailable",
            )
        if decision.allowed:
            return RiskDecision.ok()
        return RiskDecision.reject(
            decision.reason or "rejected by portfolio risk",
            code=decision.code or "portfolio_rejected",
        )

    _gate.__name__ = "portfolio_gate"
    return _gate


def _unrealized_now(book: PortfolioBook) -> float:
    total = 0.0
    for dep in book.deployments:
        for position in dep.positions.values():
            total += position.qty * ((position.last_price or 0.0) - (position.avg_price or 0.0))
    return total


def check_strategy_capital(
    user_id: str,
    strategy_id: str,
    additional_capital: float,
    *,
    storage: PortfolioStoragePort,
) -> tuple[bool, str | None]:
    """Would allocating more capital to this strategy breach its cap?

    Read by deployment creation, so the refusal happens before the row exists
    rather than as an order rejection later. No policy, or no cap in it, means
    no opinion.
    """
    service = PortfolioService(storage=storage)
    policy = service.get_policy(user_id)
    if policy is None or policy.max_capital_per_strategy is None:
        return True, None
    rows = storage.deployments(user_id)
    allocated = sum(
        float(r.get("capital") or 0.0)
        for r in rows
        if str(r.get("strategy_id") or "") == str(strategy_id)
        and str(r.get("status") or "").upper() != "STOPPED"
    )
    if allocated + float(additional_capital) > policy.max_capital_per_strategy:
        return (
            False,
            f"strategy {strategy_id} would hold {allocated + float(additional_capital):,.0f} "
            f"against a {policy.max_capital_per_strategy:,.0f} cap",
        )
    return True, None
