"""Replay paper fills into account positions, cash, and P&L."""

from __future__ import annotations

from typing import Any

from forward.core.order_models import Fill
from forward.core.paper_ledger_ports import PaperLedgerStoragePort
from forward.core.paper_models import PaperPriceSourcePort, TickPrice
from forward.core.time import utcnow


class PaperLedger:
    """Positions, cash and P&L for a paper account — folded from ``order_events``.

    There is no paper state table and that is deliberate. The order log *is* the
    state: replaying it produces the account, so a paper position cannot disagree
    with the orders that produced it, and a restart changes nothing. The schema
    was written for this — "positions and P&L are folds over those rows".

    ``Portfolio`` is reused rather than reimplemented. It already carries the
    identity ``equity == cash + market value`` and asserts it after every fill, so
    the paper account inherits a check that has been running against the backtester
    for as long as the backtester has existed.
    """

    def __init__(
        self,
        storage: PaperLedgerStoragePort,
        *,
        price_source: PaperPriceSourcePort,
    ) -> None:
        """Create the paper account use case.

        Args:
            storage: Port used to read saved executions and account activity.
            price_source: Live quote source for valuing open positions.
        """
        self._storage = storage
        self._price_source = price_source

    def portfolio(
        self,
        user_id: str,
        *,
        deployment_id: str | None = None,
        initial_cash: float | None = None,
        prices: dict[str, float] | None = None,
        mode: str | None = None,
    ) -> Any:
        """Replay the user's fills into a :class:`Portfolio`.

        ``deployment_id`` scopes the replay to one deployment, which is what a
        per-strategy capital allocation means. Without it the account is the whole
        paper book. ``mode`` (``"PAPER"``/``"LIVE"``) scopes by execution mode:
        the reconciler passes ``"LIVE"`` so paper fills never pollute the live
        comparison — the two books share one table, and an unfiltered fold
        compares the broker against trades it never saw.
        """
        portfolio, _ = self.portfolio_and_fills(
            user_id,
            deployment_id=deployment_id,
            initial_cash=initial_cash,
            prices=prices,
            mode=mode,
        )
        return portfolio

    def portfolio_and_fills(
        self,
        user_id: str,
        *,
        deployment_id: str | None = None,
        initial_cash: float | None = None,
        prices: dict[str, float] | None = None,
        mode: str | None = None,
    ) -> tuple[Any, list[Fill]]:
        """Return a book and the same scoped fill log used to build it, without a second replay read."""
        from forward.execution.portfolio import Portfolio

        if initial_cash is None:
            cash = (
                self._storage.deployment_cash(user_id, deployment_id)
                if deployment_id
                else 0.0
            )
        else:
            cash = float(initial_cash)
        portfolio = Portfolio(initial_cash=cash)

        fills = self.fills(user_id, deployment_id=deployment_id, mode=mode)
        for fill in fills:
            portfolio.apply_fill(fill)

        if prices:
            portfolio.mark(utcnow(), prices)
        return portfolio, fills


    def fills(
        self, user_id: str, *, deployment_id: str | None = None, mode: str | None = None
    ) -> list[Fill]:
        """Return the saved fills for an account.

        Args:
            user_id: Account whose executions are requested.
            deployment_id: Optional deployment filter.
            mode: Optional execution-mode filter.

        Returns:
            Fills in execution-time order.
        """
        return self._storage.fills(user_id, deployment_id=deployment_id, mode=mode)



    def snapshot(
        self,
        user_id: str,
        *,
        deployment_id: str | None = None,
        initial_cash: float | None = None,
        prices: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """The account as a JSON-able dict, for the dashboard.

        ``initial_cash`` overrides the deployment's capital, for an ad-hoc paper
        session with no deployment row. Without either, the account starts at zero
        rather than at an invented default.

        **Unpriced positions are excluded and named, not counted at zero.** A
        position whose symbol has no mark would otherwise be valued at
        ``last_price = 0``, and ``unrealized_pnl`` would report
        ``(0 - avg_price) * quantity`` — a fabricated loss of the entire cost basis,
        presented as a measurement. So the totals cover only the positions that
        could be priced, ``complete`` says whether that was all of them, and
        ``unpriced_symbols`` lists the rest. The dashboard shows the count; it does
        not show a total that quietly omits them.
        """
        snapshot, _ = self.snapshot_and_fills(
            user_id, deployment_id=deployment_id, initial_cash=initial_cash, prices=prices
        )
        return snapshot

    def snapshot_and_fills(
        self,
        user_id: str,
        *,
        deployment_id: str | None = None,
        initial_cash: float | None = None,
        prices: dict[str, float] | None = None,
    ) -> tuple[dict[str, Any], list[Fill]]:
        """Return a valued snapshot and its scoped fill log from one replay read."""
        portfolio, fills = self.portfolio_and_fills(
            user_id,
            deployment_id=deployment_id,
            initial_cash=initial_cash,
        )

        return self._snapshot_portfolio(portfolio, deployment_id, prices), fills

    def snapshots_for_deployments(
        self, user_id: str, deployments: list[dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        """Replay a list with one order read and batched event reads.

        Portfolios are request-local and use the same accounting and valuation as
        individual snapshots. No completed balance or price is cached.
        """
        from forward.execution.portfolio import Portfolio

        if not deployments:
            return {}
        portfolios = {
            row["deployment_id"]: Portfolio(initial_cash=float(row["capital"]))
            for row in deployments
        }
        fills_by_deployment = self._storage.fills_by_deployment(
            user_id, list(portfolios)
        )
        for deployment_id, fills in fills_by_deployment.items():
            for fill in fills:
                portfolios[deployment_id].apply_fill(fill)
        resolved: dict[tuple[str, str], float | None] = {}
        return {
            key: self._snapshot_portfolio(
                portfolio, key, self._marks(portfolio, _resolved=resolved)
            )
            for key, portfolio in portfolios.items()
        }

    def activity(self, user_id: str, *, limit: int) -> dict[str, Any]:
        """Return recent paper orders for the account activity feed.

        Args:
            user_id: Account whose activity is requested.
            limit: Maximum number of orders to return.

        Returns:
            The selected order fields and total matching order count.
        """
        return self._storage.activity(user_id, limit=limit)

    def _snapshot_portfolio(
        self, portfolio: Any, deployment_id: str | None, prices: dict[str, float] | None
    ) -> dict[str, Any]:
        """Build the dashboard snapshot from one replayed portfolio.

        Args:
            portfolio: Account state produced by replaying saved fills.
            deployment_id: Deployment represented by the account.
            prices: Optional current marks for open positions.

        Returns:
            Cash, positions, value, P&L, and price completeness.
        """
        marks = prices if prices is not None else self._marks(portfolio)

        rows: list[dict[str, Any]] = []
        unpriced: list[str] = []
        market_value = 0.0
        unrealized = 0.0
        gross_exposure = 0.0
        for position in portfolio.positions.values():
            if position.is_flat:
                continue
            mark = marks.get(position.instrument.symbol)
            if mark is not None:
                position.mark(mark)
                market_value += position.market_value
                unrealized += position.unrealized_pnl
                gross_exposure += position.exposure
                last_price: float | None = position.last_price
                row_value: float | None = position.market_value
                row_pnl: float | None = position.unrealized_pnl
            else:
                # The position is real and is shown. Only its *valuation* is
                # unknown, so only the valuation fields are null. Omitting the row
                # would tell the user they hold nothing, which is a different lie.
                unpriced.append(position.instrument.symbol)
                last_price = None
                row_value = None
                row_pnl = None
            rows.append(
                {
                    "symbol": position.instrument.symbol,
                    "exchange": position.instrument.exchange,
                    "quantity": position.quantity,
                    "avg_price": position.avg_price,
                    "last_price": last_price,
                    "market_value": row_value,
                    "unrealized_pnl": row_pnl,
                    "realized_pnl": (
                        position.realized_pnl if portfolio.commission_complete else None
                    ),
                    "gross_realized_pnl": position.realized_pnl,
                    "direction": position.direction,
                    "priced": mark is not None,
                }
            )
        rows.sort(key=lambda r: r["symbol"])
        unpriced.sort()

        gross_equity = portfolio.cash + market_value
        commission_complete = portfolio.commission_complete
        price_complete = not unpriced
        complete = commission_complete and price_complete
        equity = gross_equity if complete else None

        return {
            "initial_cash": portfolio.initial_cash,
            "cash": portfolio.cash if commission_complete else None,
            "cash_before_unknown_fees": portfolio.cash,
            "equity": equity,
            "equity_before_unknown_fees": gross_equity,
            "market_value": market_value,
            "realized_pnl": portfolio.realized_pnl if commission_complete else None,
            "gross_realized_pnl": portfolio.realized_pnl,
            "unrealized_pnl": unrealized if commission_complete and price_complete else None,
            "gross_unrealized_pnl": unrealized,
            "commission_paid": portfolio.commission_paid if commission_complete else None,
            # Net of frictions, which is the only figure worth showing next to a
            # gross one — and the one the research layer says decides whether a
            # strategy is worth considering at all.
            "net_realized_pnl": (
                portfolio.realized_pnl - portfolio.commission_paid
                if commission_complete
                else None
            ),
            "total_pnl": equity - portfolio.initial_cash if complete else None,
            "pnl_before_unknown_fees": gross_equity - portfolio.initial_cash,
            "pnl_complete": complete,
            "commission_complete": commission_complete,
            "gross_exposure": gross_exposure,
            "net_exposure": market_value,
            "positions": rows,
            # The honesty fields. A dashboard that ignores these is showing a
            # partial total as a whole one.
            "complete": not unpriced,
            "unpriced_symbols": unpriced,
            "deployment_id": deployment_id,
        }

    def _marks(
        self,
        portfolio: Any,
        *,
        _resolved: dict[tuple[str, str], float | None] | None = None,
    ) -> dict[str, float]:
        """Mark positions from live ticks.

        Positions without a live quote remain unpriced in the snapshot. The
        valuation never substitutes a cached daily close for a live market mark.
        """
        # Reuse each instrument's quote only within a grouped response. Missing
        # quotes are shared too; another request always resolves fresh prices.
        resolved = _resolved if _resolved is not None else {}
        marks: dict[str, float] = {}
        for position in portfolio.positions.values():
            if position.is_flat:
                continue
            symbol = position.instrument.symbol
            key = (symbol, position.instrument.exchange)
            if key not in resolved:
                live_price = self._price_source(*key)
                if isinstance(live_price, TickPrice):
                    resolved[key] = live_price.price
                else:
                    resolved[key] = live_price
            price = resolved[key]
            if price is not None:
                marks[symbol] = price
        return marks
