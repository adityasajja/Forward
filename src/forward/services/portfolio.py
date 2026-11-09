"""Build portfolio books and serve portfolio control-center reads.

The enforced order path is::

    Strategy → Strategy Risk → Portfolio Risk → OMS → Paper/Live Venue

Strategy risk is the existing per-deployment gate (``LimitsRiskGate`` over the
deployment's own folded book). Portfolio risk is this module: one gate over the
*aggregate* book — every deployment, every strategy — consulted inside
``OrderService.validate`` after the strategy gate passes and before anything
reaches ``RISK_APPROVED``. There is no second path to submission, so neither a
strategy nor the learning tier can bypass it: both place through the OMS.

What this module owns, and what it explicitly does not
-------------------------------------------------------
* **Capital allocation** is the deployment row's ``capital`` (it already
  exists). This module reads it, sums it, and refuses to over-allocate a
  strategy — it never sets it.
* **Limits** live in one ``portfolio_policies`` row per user. No row means no
  portfolio limits: the gate passes everything through, which is the
  pre-policy behaviour, and every dashboard figure is still computed.
* **Conflicts are displayed always, enforced per policy.** Opposite holdings
  across deployments are shown whatever the mode; whether an incoming order
  against them fills is the operator's configured rule (``reject``,
  ``priority`` or ``net``). The system never picks a winner on its own: ties
  fail closed, and "net" still counts gross exposure against the size limits.
* **Exits are never trapped.** An order that strictly reduces its own arm's
  position bypasses the exposure and conflict checks — blocking an exit would
  lock a loss in place. The strategy gate still applies.
* **No statistics, no learning, no promotion.** Figures are sums, shares and
  counts. The learning tier later reads the retained entry-time context, but
  nothing here analyses it.

Units: notionals (exposure, stock, loss, capital) are rupees; sector,
correlated and concentration figures are fractions of total allocated capital
(0.40 = 40%). Percentage-style whole numbers (``40`` for 40%) are refused at
the door — that mixup is the most likely misconfiguration, and a 4000%
sector cap would silently disable the check.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from forward.analytics.learning_metrics import max_drawdown
from forward.core.portfolio_ports import (
    PortfolioStoragePort,
)
from forward.core.session import IST
from forward.services.portfolio_dashboard import (
    active_limits,
    concentration_warnings,
    describe_conflicts,
    top_exposures,
)
from forward.services.portfolio_models import (
    BookPosition,
    DeploymentSlice,
    PortfolioBook,
    _apply_fill,
    _clean,
    _side_sign,
    sector_resolver,
)
from forward.services.portfolio_policy import (
    PolicyError,
    PortfolioPolicy,
    default_policy,
    policy_from_dict,
)

logger = logging.getLogger("forward.services.portfolio")


# ---------------------------------------------------------------------------
# policy storage and book aggregation
# ---------------------------------------------------------------------------


class PortfolioService:
    """Read and update portfolio policies, books, and control-center data."""

    def __init__(self, storage: PortfolioStoragePort) -> None:
        """Create portfolio use cases with an explicit storage adapter.

        Args:
            storage: Port for portfolio policies, fills, deployments, and journal rows.
        """
        self._storage = storage

    # ------------------------------------------------------------- policy store
    def get_policy(self, user_id: str) -> PortfolioPolicy | None:
        """The stored policy, or ``None`` when the user never set one.

        ``None`` — not a default object — is what tells the gate to pass
        everything through: "no policy" and "a policy with every limit off"
        are different states, and the dashboard shows which one holds.
        """
        stored_policy = self._storage.policy(user_id)
        if stored_policy is None:
            return None
        try:
            payload = json.loads(stored_policy)
        except (TypeError, ValueError) as error:
            raise PolicyError(
                f"stored portfolio policy for {user_id} is unreadable",
                code="stored_policy_invalid",
                status=500,
            ) from error
        try:
            return policy_from_dict(payload)
        except PolicyError as error:
            raise PolicyError(
                f"stored portfolio policy for {user_id} is invalid: {error}",
                code="stored_policy_invalid",
                status=500,
            ) from error

    def set_policy(
        self, user_id: str, payload: dict[str, Any], *, actor: str, reason: str
    ) -> dict[str, Any]:
        """Validate and store the policy. Wholesale replacement, reason required.

        Changing what the platform may risk is consequential, so — like the
        kill switch — it needs a who and a why. Unknown fields are refused
        rather than dropped: a limit the caller believes they set must not
        silently vanish.
        """
        reason = (reason or "").strip()
        if not reason:
            raise PolicyError(
                "setting the portfolio policy requires a reason",
                code="reason_required",
                status=400,
            )
        policy = policy_from_dict(payload)
        now = self._storage.save_policy(
            user_id,
            json.dumps(policy.as_dict()),
            actor=(actor or ""),
            reason=reason,
        )
        return {"user_id": user_id, "policy": policy.as_dict(), "updated_at": now}

    # ------------------------------------------------------------------- book
    def build_book(self, user_id: str, *, prices: dict[str, float] | None = None) -> PortfolioBook:
        """Fold the user's fills into per-deployment lots, with capital attached.

        Marks come from ``prices`` where given, else the last fill price for
        the symbol — a documented approximation, and symbols with neither are
        named in ``symbols_without_price`` and excluded from value totals
        rather than valued at zero.
        """
        marks = {str(k).strip().upper(): float(v) for k, v in (prices or {}).items()}
        deployments = self._storage.deployments(user_id)
        fills = self._storage.fills(user_id)

        slices: dict[str, DeploymentSlice] = {}
        for dep in deployments:
            dep_id = str(dep.get("deployment_id"))
            slices[dep_id] = DeploymentSlice(
                deployment_id=dep_id,
                strategy_id=dep.get("strategy_id"),
                strategy_version=dep.get("strategy_version"),
                mode=dep.get("mode"),
                status=dep.get("status"),
                capital=float(dep.get("capital") or 0.0),
            )

        for dep in slices.values():
            dep.cash = dep.capital
        commission_complete = True
        for fill in fills:
            if fill.get("commission") is None:
                commission_complete = False
            dep_id = fill.get("deployment_id")
            if not dep_id or dep_id not in slices:
                continue
            symbol = _clean(fill.get("symbol"))
            if not symbol:
                continue
            positions = slices[dep_id].positions
            position = positions.get(symbol)
            if position is None:
                position = BookPosition(
                    symbol=symbol,
                    deployment_id=dep_id,
                    strategy_id=slices[dep_id].strategy_id,
                )
                positions[symbol] = position
            quantity, price = _apply_fill(position, fill)
            try:
                commission_value = fill["commission"]
                commission = None if commission_value is None else float(commission_value)
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(
                    f"portfolio fill for {symbol} has invalid commission"
                ) from error
            notional = quantity * price
            if commission is None:
                commission_complete = False
                slices[dep_id].cash = None
                continue
            if slices[dep_id].cash is None:
                continue
            if _side_sign(fill.get("side")) > 0:
                slices[dep_id].cash -= notional + commission
            else:
                slices[dep_id].cash += notional - commission

        no_price: list[str] = []
        for dep in slices.values():
            for position in dep.positions.values():
                mark = marks.get(position.symbol)
                if mark is not None and mark > 0:
                    position.last_price = mark
                elif not position.last_price and position.symbol not in no_price:
                    no_price.append(position.symbol)

        total_capital = sum(
            dep.capital for dep in slices.values() if str(dep.status or "").upper() != "STOPPED"
        )
        return PortfolioBook(
            deployments=list(slices.values()),
            total_capital=total_capital,
            symbols_without_price=sorted(no_price),
            commission_complete=commission_complete,
        )

    def fills_for_user(self, user_id: str) -> list[dict[str, Any]]:
        """Return the user's fills for entry-time portfolio replay.

        Args:
            user_id: Account whose fills are requested.

        Returns:
            Incremental fill rows in fill-time order.
        """
        return self._storage.fills(user_id)

    def deployments_for_user(self, user_id: str) -> list[dict[str, Any]]:
        """Return the user's deployments for entry-time capital context.

        Args:
            user_id: Account whose deployments are requested.

        Returns:
            The account's deployment rows.
        """
        return self._storage.deployments(user_id)

    # ------------------------------------------------------- dashboard figures
    def realized_by_deployment(self, user_id: str) -> dict[str, dict[str, Any]]:
        """Closed-trade P&L per deployment from the journal. Open episodes excluded:
        an unrealised figure is not a realised one, and the book already reports
        the open side separately."""
        episodes = self._storage.iter_journal_episodes(user_id, closed_only=False)
        commission_complete = all(
            fill.get("commission") is not None for fill in self._storage.fills(user_id)
        )
        out: dict[str, dict[str, Any]] = {}
        for episode in episodes:
            key = str(episode.get("deployment_id") or "UNKNOWN")
            entry = out.setdefault(
                key,
                {
                    "realized": 0.0,
                    "trades_closed": 0,
                    "trades_open": 0,
                    "curve": [],
                    "pnl_complete": commission_complete,
                },
            )
            if episode.get("exit_ts") is None:
                entry["trades_open"] += 1
                continue
            net = episode.get("net_pnl")
            entry["trades_closed"] += 1
            if net is None:
                continue
            pnl = _journal_pnl(episode)
            entry["realized"] += pnl
            entry["curve"].append((episode.get("exit_ts"), pnl))
        if not commission_complete:
            for entry in out.values():
                entry["realized"] = None
                entry["curve"] = []
        return out

    # ------------------------------------------------------- control center
    def control_center(
        self,
        user_id: str,
        *,
        prices: dict[str, float] | None = None,
        sector_table: Mapping[str, str],
    ) -> dict[str, Any]:
        """Everything the Portfolio Control Center renders, in one read.

        Objective figures only: deployment metrics are ordered by allocated
        capital, never ranked — there is no "best" here, and background text
        says so. Warnings fire on proximity to a *configured* limit; with no
        limit configured there is nothing to be proximate to, so nothing warns.
        """
        policy = self.get_policy(user_id)
        effective = policy or default_policy()
        book = self.build_book(user_id, prices=prices)
        sector_fn = sector_resolver(sector_table)
        realized = self.realized_by_deployment(user_id)
        today_realized = self.realized_today(user_id)

        names = {
            str(row["strategy_id"]): str(row.get("name") or row["strategy_id"])
            for row in self._storage.strategies(user_id)
        }

        unrealized_by_deployment: dict[str, float] = {}
        for dep in book.deployments:
            for position in dep.positions.values():
                key = dep.deployment_id
                unrealized_by_deployment[key] = unrealized_by_deployment.get(
                    key, 0.0
                ) + position.qty * ((position.last_price or 0.0) - (position.avg_price or 0.0))
        unrealized_total = sum(unrealized_by_deployment.values())
        pnl_complete = book.commission_complete
        portfolio_total = (
            sum(v["realized"] for v in realized.values()) + unrealized_total
            if pnl_complete
            else None
        )

        strategies: list[dict[str, Any]] = []
        for dep in sorted(book.deployments, key=lambda d: -d.capital):
            key = str(dep.strategy_id or "UNKNOWN")
            stats = realized.get(
                dep.deployment_id,
                {
                    "realized": 0.0,
                    "trades_closed": 0,
                    "trades_open": 0,
                    "curve": [],
                    "pnl_complete": pnl_complete,
                },
            )
            used = sum(abs(p.value) for p in dep.positions.values())
            unrealized = unrealized_by_deployment.get(dep.deployment_id, 0.0)
            total = stats["realized"] + unrealized if pnl_complete else None
            curve = [net for _ts, net in sorted(stats["curve"], key=lambda t: str(t[0]))]
            if unrealized and pnl_complete:
                curve = [*curve, unrealized]
            strategies.append(
                {
                    "strategy_id": key,
                    "strategy_name": names.get(key, key),
                    "allocated": dep.capital,
                    "used": round(used, 2),
                    "available": round(dep.capital - used, 2),
                    "exposure": round(used, 2),
                    "realized": round(stats["realized"], 2) if pnl_complete else None,
                    "unrealized": round(unrealized, 2) if pnl_complete else None,
                    "total_pnl": round(total, 2) if pnl_complete else None,
                    "gross_unrealized_pnl": round(unrealized, 2),
                    "return_pct": (
                        round(total / dep.capital * 100.0, 2)
                        if pnl_complete and dep.capital
                        else None
                    ),
                    "drawdown": max_drawdown(curve) if pnl_complete else None,
                    "trades_closed": stats["trades_closed"],
                    "trades_open": stats["trades_open"],
                    "contribution": (
                        round(total / portfolio_total, 4)
                        if pnl_complete and portfolio_total
                        else None
                    ),
                    "deployment_id": dep.deployment_id,
                    "deployment_status": dep.status,
                }
            )

        gross = book.gross_exposure()
        long_value = sum(
            p.value for dep in book.deployments for p in dep.positions.values() if p.value > 0
        )
        cash = (
            sum(dep.cash for dep in book.deployments if dep.cash is not None)
            if book.commission_complete
            else None
        )
        capital = book.total_capital

        open_positions: list[dict[str, Any]] = []
        net_by_symbol: dict[str, float] = {}
        val_by_symbol: dict[str, float] = {}
        deps_by_symbol: dict[str, set[str]] = {}
        for dep in book.deployments:
            for position in dep.positions.values():
                net_by_symbol[position.symbol] = (
                    net_by_symbol.get(position.symbol, 0.0) + position.qty
                )
                val_by_symbol[position.symbol] = (
                    val_by_symbol.get(position.symbol, 0.0) + position.value
                )
                deps_by_symbol.setdefault(position.symbol, set()).add(dep.deployment_id)
        for symbol in sorted(val_by_symbol):
            if abs(net_by_symbol.get(symbol, 0.0)) <= 1e-9 and abs(val_by_symbol[symbol]) <= 1e-9:
                continue
            open_positions.append(
                {
                    "symbol": symbol,
                    "qty": round(net_by_symbol[symbol], 4),
                    "value": round(val_by_symbol[symbol], 2),
                    "sector": sector_fn(symbol),
                    "deployments": sorted(deps_by_symbol[symbol]),
                    "pct_of_capital": round(val_by_symbol[symbol] / capital, 4)
                    if capital
                    else None,
                }
            )

        sectors: dict[str, dict[str, Any]] = {}
        for symbol, value in val_by_symbol.items():
            sector = sector_fn(symbol)
            entry = sectors.setdefault(sector, {"value": 0.0})
            entry["value"] += abs(value)
        for entry in sectors.values():
            entry["value"] = round(entry["value"], 2)
            entry["pct"] = round(entry["value"] / capital, 4) if capital else None
        strat_exposure: dict[str, float] = {}
        for dep in book.deployments:
            key = str(dep.strategy_id or "UNKNOWN")
            strat_exposure[key] = strat_exposure.get(key, 0.0) + sum(
                abs(p.value) for p in dep.positions.values()
            )

        warnings = concentration_warnings(
            val_by_symbol, sectors, strat_exposure, capital, effective
        )
        if book.symbols_without_price:
            warnings.append(
                f"{len(book.symbols_without_price)} symbol(s) have no price and are "
                f"excluded from value totals: {', '.join(book.symbols_without_price[:5])}"
            )

        conflicts = describe_conflicts(book)
        limits = active_limits(book, effective, capital, gross)

        today_total = today_realized + unrealized_total
        journal_curve = _realized_curve(self._storage, user_id)
        if unrealized_total:
            journal_curve = [*journal_curve, unrealized_total]

        limitations: list[str] = []
        if policy is None:
            limitations.append(
                "no portfolio policy is configured: configured limits are off; "
                "fee completeness still gates new exposure"
            )
        if book.symbols_without_price:
            limitations.append(
                "value totals exclude unpriced symbols (last fill price unknown "
                "and no mark supplied)"
            )
        if not pnl_complete:
            limitations.append(
                "some execution fees are unknown: net P&L and cash are incomplete; "
                "new exposure is blocked while exposure-reducing orders remain allowed"
            )

        return {
            "generated_at": datetime.now(UTC).replace(tzinfo=None).isoformat(),
            "policy_configured": policy is not None,
            "policy": (policy or default_policy()).as_dict(),
            "capital": {
                "total": round(capital, 2),
                "deployed": round(
                    sum(
                        d.capital
                        for d in book.deployments
                        if str(d.status or "").upper() == "RUNNING"
                    ),
                    2,
                ),
                "used": round(
                    sum(abs(p.value) for dep in book.deployments for p in dep.positions.values()), 2
                ),
                "available": round(capital - gross, 2),
                "cash": round(cash, 2) if cash is not None else None,
                "cash_utilization": (
                    round(1.0 - cash / capital, 4)
                    if pnl_complete and cash is not None and capital
                    else None
                ),
            },
            "exposure": {
                "gross": round(gross, 2),
                "long": round(long_value, 2),
                "short": round(gross - long_value, 2),
                "net": round(sum(val_by_symbol.values()), 2),
            },
            "today": {
                "realized": round(today_realized, 2) if pnl_complete else None,
                "unrealized": round(unrealized_total, 2) if pnl_complete else None,
                "total": round(today_total, 2) if pnl_complete else None,
                "gross_unrealized": round(unrealized_total, 2),
            },
            "drawdown": {
                "value": max_drawdown(journal_curve) if pnl_complete else None,
                "method": (
                    "peak-to-trough of cumulative closed-trade net P&L with "
                    "current unrealized appended"
                ),
            },
            "open_positions": open_positions,
            "sectors": sectors,
            "strategies": strategies,
            "concentrations": {
                "stocks": top_exposures(val_by_symbol, capital, 5),
                "sectors": top_exposures({k: v["value"] for k, v in sectors.items()}, capital, 5),
                "strategies": top_exposures(strat_exposure, capital, 5),
                "warnings": warnings,
            },
            "conflicts": conflicts,
            "limits": limits,
            "unpriced_symbols": book.symbols_without_price,
            "pnl_complete": pnl_complete,
            "commission_complete": book.commission_complete,
            "limitations": limitations,
            "advisory_only": True,
            "applies_changes": False,
        }

    def realized_today(self, user_id: str, *, now: datetime | None = None) -> float:
        """Today's closed-trade P&L across the book, in IST session date."""
        moment = now or datetime.now(UTC)
        today = moment.astimezone(IST).date()
        total = 0.0
        episodes = self._storage.iter_journal_episodes(user_id, closed_only=True)
        for episode in episodes:
            exit_ts = episode.get("exit_ts")
            stamp = None
            if hasattr(exit_ts, "date"):
                # Appdb stamps are naive UTC; "today" is an IST session date.
                # Comparing the naive UTC calendar day puts 00:00–05:30 IST
                # closes on the wrong day.
                stamp = (
                    exit_ts.replace(tzinfo=UTC).astimezone(IST).date()
                    if exit_ts.tzinfo is None
                    else exit_ts.astimezone(IST).date()
                )
            if stamp != today or episode.get("net_pnl") is None:
                continue
            total += _journal_pnl(episode)
        return total


# ---------------------------------------------------------------------------
# gate — the portfolio check, after the strategy gate, before the OMS
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# control center — one read-only view of the whole paper book
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
def _realized_curve(storage: PortfolioStoragePort, user_id: str) -> list[float]:
    """Cumulative closed-trade net P&L by exit order, for the drawdown read."""
    episodes = storage.iter_journal_episodes(user_id, closed_only=True)
    stamps: list[tuple[str, float]] = []
    for episode in episodes:
        if episode.get("net_pnl") is None:
            continue
        stamps.append((str(episode.get("exit_ts") or ""), _journal_pnl(episode)))
    stamps.sort(key=lambda t: t[0])
    running = 0.0
    curve = []
    for _ts, net in stamps:
        running += net
        curve.append(round(running, 2))
    return curve


def _journal_pnl(episode: dict[str, Any]) -> float:
    """Read one persisted journal P&L value without hiding bad data."""
    value = episode["net_pnl"]
    try:
        pnl = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"journal episode {episode.get('episode_id', '<unknown>')} has invalid net_pnl"
        ) from error
    if not math.isfinite(pnl):
        raise ValueError(
            f"journal episode {episode.get('episode_id', '<unknown>')} has non-finite net_pnl"
        )
    return pnl


# ---------------------------------------------------------------------------
# gate factory — the callable the OMS consults inside validate()
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# learning replay — portfolio context at each entry, from the order log
# ---------------------------------------------------------------------------
