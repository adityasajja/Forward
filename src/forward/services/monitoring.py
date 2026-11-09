"""The deployment monitoring surface — one payload for one deployment's screen.

Why this is its own module
--------------------------

``DeploymentService`` (``services/deployments.py``) owns the *account*: it folds the
order log into positions, cash, P&L and exposure. That is the right home for
arithmetic that must agree with the venue's own view of the book.

This module owns the *narrative*: what the deployment is doing, why it is or is
not trading, and the causal chain from a signal to a position update. Those are
different questions with different failure modes. The account can be perfectly
correct while the deployment is silently inert — which is exactly the state the
runner's ``blocked_reason`` exists to expose, and it has no place on a P&L
snapshot.

The timeline is the piece worth reading. A monitoring screen that shows
positions, orders and fills as three separate lists asks the operator to
reconstruct the causal chain in their head, and a chain assembled by eye is one
that gets assembled wrongly. ``timeline()`` reads the append-only event log and
emits one ordered list where each entry says what caused it.
"""

from __future__ import annotations

import dataclasses
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from forward.core.calendar_ports import MarketCalendarPort
from forward.core.order_ports import (
    OrderMonitoringStoragePort,
)
from forward.core.paper_ledger_ports import PaperMonitoringLedgerPort
from forward.core.paper_monitoring_ports import DeploymentMonitoringStoragePort
from forward.core.paper_runner_ports import DeploymentMonitorRunnerPort
from forward.core.risk_ports import (
    RiskStatePort,
)
from forward.services.monitoring_timeline import (
    TimelineEntry,
    as_datetime,
    loads_json,
    timeline_from_log,
)


class MonitoringError(Exception):
    """A monitoring read that cannot be served."""

    def __init__(self, message: str, *, code: str = "monitoring_error", status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def _jsonable(value: Any) -> Any:
    """A value the JSON encoder can emit, without changing what it means.

    ``RiskLimits`` is a dataclass holding an ``inf`` sentinel for "no limit". A
    repr string would be unreadable in the UI and a bare ``inf`` is not valid
    JSON, so the sentinel becomes ``None`` — which in this envelope means
    *unbounded*, the same thing the limit does. Anything else is passed through
    untouched and left to the framework's encoder.
    """
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, (str, int, float)):
        return value
    return str(value)


def _read_log(
    storage: OrderMonitoringStoragePort,
    user_id: str,
    deployment_id: str,
    *,
    limit: int = 500,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """This deployment's orders and their events, in one or two queries.

    The adapter reads orders and events in batches, and grouping the event rows
    here means the monitor costs two queries instead of one per order. ``seq`` is
    kept as the tiebreaker because a submit and its fill can share a timestamp,
    and the timeline's whole value is being in the right order.
    """
    orders, events = storage.monitoring_order_log(
        user_id,
        deployment_id,
        order_limit=limit,
        event_limit=limit * 10,
    )

    by_order: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        by_order.setdefault(str(event.get("order_id")), []).append(event)
    for rows in by_order.values():
        # ``ts`` mixes naive and aware ISO forms across writers; string
        # sorting misorders them ("+05:30" vs " "). Parse first, fall back
        # to the raw string only when unparseable.
        rows.sort(key=lambda e: (_sort_ts(e.get("ts")), int(e.get("seq") or 0)))

    orders.sort(key=lambda o: (_sort_ts(o.get("created_at")), str(o.get("order_id"))))
    return orders, by_order


def _sort_ts(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).isoformat()
        except ValueError:
            return value
    return "" if value is None else str(value)


class DeploymentMonitor:
    """Read-only composition over the deployment, its orders and its events.

    Deliberately read-only: every method here is a query. Nothing in this module
    can change a deployment's state, so a monitoring bug can misreport but cannot
    mis-trade.
    """

    def __init__(
        self,
        *,
        portfolio_storage: DeploymentMonitoringStoragePort,
        order_storage: OrderMonitoringStoragePort,
        ledger: PaperMonitoringLedgerPort,
        risk_state: RiskStatePort,
        runner: DeploymentMonitorRunnerPort,
        calendar: MarketCalendarPort,
    ) -> None:
        """Create the read-only monitor.

        Args:
            portfolio_storage: Deployment, journal, and account snapshots.
            order_storage: Orders and their event history.
            ledger: Paper ledger used to value fills and positions.
            risk_state: Current platform risk limits and kill-switch state.
            runner: Process runner that reports deployment loop state.
            calendar: Exchange calendar used to report market session state.
        """
        self._portfolio_storage = portfolio_storage
        self._order_storage = order_storage
        self.ledger = ledger
        #: The runner whose live loops this monitor reports on. Defaults to the
        #: process-wide one, which is the real answer in production; injected so
        #: a caller can point the monitor at the runner it actually started,
        #: rather than at a second one that knows nothing about its deployments.
        self._runner = runner
        self._risk_state = risk_state
        self._calendar = calendar

    # ------------------------------------------------------------------
    # deployment resolution
    # ------------------------------------------------------------------
    def _require(self, user_id: str, deployment_id: str) -> dict[str, Any]:
        """The deployment row, or a 404. Owner-scoped, always."""
        row = self._portfolio_storage.deployment(user_id, deployment_id)
        if row is None:
            raise MonitoringError("deployment not found", code="not_found", status=404)
        return row

    # ------------------------------------------------------------------
    # status
    # ------------------------------------------------------------------
    def status(
        self,
        user_id: str,
        deployment_id: str,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Return the deployment status and its trading diagnostics.

        Args:
            user_id: Account that owns the deployment.
            deployment_id: Deployment whose status is requested.
            now: Optional clock value used for market-session checks.

        Returns:
            The deployment status fields used by the monitoring screen.
        """
        row = self._require(user_id, deployment_id)
        return self._status_from_row(user_id, deployment_id, row, now=now)

    def _status_from_row(
        self,
        user_id: str,
        deployment_id: str,
        row: dict[str, Any],
        *,
        now: datetime | None = None,
        pnl_info: dict[str, Any] | None = None,
        trades_info: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Everything the header of the monitoring screen shows.

        ``trading`` is the field that matters and the one that is easy to get
        wrong. A deployment can be ``RUNNING`` and still place no orders — because
        its rules could not be resolved, because the market is shut, or because it
        has no universe. Reporting only the row's ``status`` would show "running"
        for a deployment that is not running, so ``blocked_reason`` and
        ``in_market_hours`` are returned alongside it and the UI is expected to
        show all three.

        Args:
            user_id: Account that owns the deployment.
            deployment_id: Deployment whose status is requested.
            row: Deployment row already loaded for this request.
            now: Optional clock value used for market-session checks.
            pnl_info: P&L already calculated for a combined overview.
            trades_info: Trade summary already loaded for a combined overview.

        Returns:
            The deployment status fields used by the monitoring screen.
        """
        config = loads_json(row["config"])
        if not isinstance(config, dict):
            raise ValueError(f"deployment {deployment_id!r} has invalid stored config")

        status = row["status"]
        loop_status = self._runner.deployment_status(deployment_id)

        blocked_reason = loop_status.get("blocked_reason") if loop_status else None
        open_now = self._calendar.is_market_open(now)

        runner_state = loop_status.get("state") if loop_status else None
        last_tick_dt = loop_status.get("last_tick_time") if loop_status else None
        last_tick_age = loop_status.get("last_tick_age_seconds") if loop_status else None
        max_tick_age = loop_status.get("max_tick_age_seconds") if loop_status else None
        skipped_reason = loop_status.get("skipped_reason") if loop_status else None
        last_risk = loop_status.get("last_risk_decision") if loop_status else None
        last_order = loop_status.get("last_order") if loop_status else None
        last_fill = loop_status.get("last_fill") if loop_status else None

        if status != "RUNNING":
            trading = False
            why = f"deployment is {status}"
            diagnostic_state = status.lower()
        elif loop_status is None:
            trading = False
            why = "the runner has not attached a loop yet"
            diagnostic_state = "waiting_for_loop"
        elif blocked_reason:
            trading = False
            why = blocked_reason
            diagnostic_state = "blocked"
        elif not open_now:
            trading = False
            why = skipped_reason or "the cash session is closed"
            diagnostic_state = "market_closed"
        elif runner_state == "ERROR":
            trading = False
            why = skipped_reason or "system error"
            diagnostic_state = "system_error"
        elif runner_state == "WAITING_FOR_TICKS":
            trading = False
            if last_tick_age is not None and last_tick_age > max_tick_age:
                diagnostic_state = "stale_tick"
                why = f"stale tick received ({last_tick_age:.1f}s old, max allowed {max_tick_age:.0f}s)"
            else:
                diagnostic_state = "no_live_tick"
                why = "no live tick received"
        elif last_tick_dt is None:
            trading = False
            diagnostic_state = "no_live_tick"
            why = "no live tick received yet"
        elif last_tick_age is not None and last_tick_age > max_tick_age:
            trading = False
            diagnostic_state = "stale_tick"
            why = f"tick is stale ({last_tick_age:.1f}s old)"
        else:
            trading = True
            why = None
            if last_risk and not last_risk.get("approved"):
                diagnostic_state = "risk_rejected"
                why = last_risk.get("reason") or "risk rejected signal"
            elif last_order and last_order.get("status") == "REJECTED":
                diagnostic_state = "order_rejected"
                why = last_order.get("reason") or "order rejected"
            elif last_order and last_order.get("status") in ("OPEN", "SUBMITTED", "ACCEPTED"):
                diagnostic_state = "order_waiting_for_fill"
            elif last_fill and (
                not last_order or last_order.get("order_id") == last_fill.get("order_id")
            ):
                diagnostic_state = "paper_fill_completed"
            elif skipped_reason and "no signal" in skipped_reason.lower():
                diagnostic_state = "no_signal"
            else:
                diagnostic_state = "running"

        if trades_info is None:
            trades_info = self.trades(user_id, deployment_id)
        if pnl_info is None:
            pnl_info = self.pnl(user_id, deployment_id)

        return {
            "deployment_id": deployment_id,
            "status": status,
            "mode": row["mode"],
            "strategy_id": row["strategy_id"],
            "strategy_version": row["strategy_version"],
            "capital": row["capital"],
            "symbols": config["symbols"],
            "exchange": config["exchange"],
            "timeframe": config["timeframe"],
            "started_at": row.get("started_at"),
            "stopped_at": row.get("stopped_at"),
            "stop_reason": row.get("stop_reason"),
            "created_at": row.get("created_at"),
            # The fields that together answer "is it trading?" and why.
            "trading": trading,
            "not_trading_because": why,
            "diagnostic_state": diagnostic_state,
            "runner_state": runner_state,
            "blocked_reason": blocked_reason,
            "skipped_reason": skipped_reason,
            "in_market_hours": open_now,
            "market_open": open_now,
            "runner_running": self._runner.running,
            "loop_attached": loop_status is not None,
            "last_tick_time": last_tick_dt.isoformat() if last_tick_dt else None,
            "last_tick_age_seconds": last_tick_age,
            "tick_age_seconds": last_tick_age,
            "last_strategy_evaluation": (
                loop_status["last_strategy_evaluation"].isoformat()
                if loop_status and loop_status.get("last_strategy_evaluation")
                else None
            ),
            "last_signal": loop_status.get("last_signal") if loop_status else None,
            "last_risk_decision": last_risk,
            "last_order": last_order,
            "last_fill": last_fill,
            "skipped_evaluations_count": (
                loop_status.get("skipped_evaluations_count", 0) if loop_status else 0
            ),
            "skipped_fills_count": loop_status.get("skipped_fills_count", 0) if loop_status else 0,
            "last_pass": (loop_status.get("last_pass") or {}) if loop_status else None,
            "trade_count": trades_info.get("total", 0),
            "open_trades_count": trades_info.get("open_count", 0),
            "today_pnl": pnl_info.get("today_pnl"),
            "cumulative_pnl": pnl_info.get("total_pnl"),
            "equity": pnl_info.get("equity"),
        }

    # ------------------------------------------------------------------
    # pnl, with today separated from the lifetime figure
    # ------------------------------------------------------------------
    def pnl(
        self,
        user_id: str,
        deployment_id: str,
        *,
        prices: dict[str, float] | None = None,
        _book: tuple[dict[str, Any], list[Any]] | None = None,
    ) -> dict[str, Any]:
        """Lifetime and *today's* P&L, kept distinct.

        Today's P&L cannot be read off a stored equity curve, because the paper
        engine has no curve — it has a log. It is the change in account equity
        since the session boundary, which is the only definition that answers
        "how is it doing today" without inventing a baseline.

        The boundary is IST midnight, matching the exchange the deployment trades
        on. When the log holds no fill before today, today's figure *is* the
        lifetime figure and ``today_since`` is ``None``: there is no earlier
        equity to subtract, and reporting 0 as the day's P&L would understate a
        first-day gain as nothing. ``today_pnl`` is returned as ``None`` in that
        case, never ``0.0`` — the two mean different things and the UI must be
        able to tell them apart.
        """
        from forward.services.runner import IST

        row = self._require(user_id, deployment_id)
        snapshot, fills = (
            _book
            if _book is not None
            else self.ledger.snapshot_and_fills(user_id, deployment_id=deployment_id, prices=prices)
        )

        day_start = datetime.combine(datetime.now(IST).date(), datetime.min.time(), tzinfo=IST)
        capital = float(row["capital"])
        equity_then = self._equity_from_fills(fills, capital, day_start)

        if equity_then is None:
            today_pnl = None
            today_pct = None
            today_since = None
        else:
            today_pnl = snapshot["equity"] - equity_then
            today_pct = (snapshot["equity"] / equity_then - 1.0) * 100.0 if equity_then else None
            today_since = day_start.isoformat()

        return {
            **snapshot,
            "deployment_id": deployment_id,
            "capital": capital,
            "today_pnl": today_pnl,
            "today_pct": today_pct,
            "today_since": today_since,
            # Both figures are against the *allocated* capital, which is what a
            # deployment is judged on — not against the initial_cash the ledger
            # folded to, and not against whatever cash remained.
            "total_pnl": (
                snapshot["equity"] - capital
                if capital and snapshot["equity"] is not None
                else None
            ),
            "total_pct": (
                (snapshot["equity"] / capital - 1.0) * 100.0
                if capital and snapshot["equity"] is not None
                else None
            ),
            "exposure_pct": (
                snapshot["gross_exposure"] / snapshot["equity"] * 100.0
                if snapshot["equity"] is not None and snapshot["equity"]
                else None
            ),
        }

    @staticmethod
    def _equity_from_fills(fills: list[Any], capital: float, moment: datetime) -> float | None:
        from forward.execution.portfolio import Portfolio

        fills = [
            f for f in fills if (ts := as_datetime(getattr(f, "ts", None))) is not None and ts < moment
        ]
        if not fills:
            return None

        portfolio = Portfolio(initial_cash=capital)
        marks: dict[str, float] = {}
        for fill in fills:
            portfolio.apply_fill(fill)
            # A fill is a trade at that price, so it is the mark as of then.
            marks[fill.instrument.symbol] = fill.price

        for symbol, price in marks.items():
            position = portfolio.positions.get(symbol)
            if position is not None and not position.is_flat:
                position.mark(price)
        return portfolio.equity if portfolio.commission_complete else None

    # ------------------------------------------------------------------
    # orders and fills
    # ------------------------------------------------------------------
    def orders(self, user_id: str, deployment_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        """Orders for this deployment, newest first, with their event count."""
        self._require(user_id, deployment_id)
        rows, by_order = _read_log(self._order_storage, user_id, deployment_id, limit=limit)
        return self._orders_from_log(rows, by_order)

    def _orders_from_log(
        self, rows: list[dict[str, Any]], by_order: dict[str, list[dict[str, Any]]]
    ) -> list[dict[str, Any]]:
        out = [
            {
                "order_id": row["order_id"],
                "symbol": row.get("symbol"),
                "side": row.get("side"),
                "quantity": row.get("quantity"),
                "order_type": row.get("order_type"),
                "limit_price": row.get("limit_price"),
                "status": row.get("status"),
                # The column is ``filled_quantity``; the event payload calls the
                # same thing ``filled_qty``. Renaming it here to the event's
                # vocabulary would make this read disagree with the row it came
                # from, so the row's own name is used.
                "filled_quantity": row.get("filled_quantity"),
                "avg_fill_price": row.get("avg_fill_price"),
                "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at"),
                "strategy_id": row.get("strategy_id"),
                "strategy_version": row.get("strategy_version"),
                "signal_reason": row.get("signal_reason"),
                "events": len(by_order.get(str(row["order_id"]), [])),
            }
            for row in rows
        ]
        out.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
        return out

    def fills(self, user_id: str, deployment_id: str) -> list[dict[str, Any]]:
        """Execution events, newest first."""
        self._require(user_id, deployment_id)
        fills = self.ledger.fills(user_id, deployment_id=deployment_id)
        return self._fills_from_log(fills)

    @staticmethod
    def _fills_from_log(fills: list[Any]) -> list[dict[str, Any]]:
        out = [
            {
                "order_id": getattr(f, "order_id", None),
                "symbol": getattr(f, "symbol", None),
                "side": getattr(f, "side", None),
                "quantity": getattr(f, "quantity", None),
                "price": getattr(f, "price", None),
                "commission": getattr(f, "commission", None),
                "ts": getattr(f, "ts", None),
                "value": (
                    float(getattr(f, "quantity", 0) or 0) * float(getattr(f, "price", 0) or 0)
                ),
            }
            for f in fills
        ]
        out.sort(key=lambda r: str(r.get("ts") or ""), reverse=True)
        return out

    # ------------------------------------------------------------------
    # signals
    # ------------------------------------------------------------------
    def signals(
        self, user_id: str, deployment_id: str, *, limit: int = 200
    ) -> list[dict[str, Any]]:
        """Signals this deployment generated, derived from the order log.

        There is no separate signal table, and there should not be: a signal that
        did not become an order is not actionable and recording it would fill a
        screen with rows nothing acted on. What is recorded is the signal that
        *did* produce an order, on the order's creation event, together with the
        rule and reason the runner stamped on it.
        """
        self._require(user_id, deployment_id)
        rows, by_order = _read_log(self._order_storage, user_id, deployment_id, limit=limit)
        return self._signals_from_log(rows, by_order)

    def _signals_from_log(
        self, rows: list[dict[str, Any]], by_order: dict[str, list[dict[str, Any]]]
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for row in rows:
            for event in by_order.get(str(row["order_id"]), []):
                if event.get("to_status") != "NEW":
                    continue
                raw = loads_json(event.get("raw")) or {}
                if not isinstance(raw, dict):
                    raw = {}
                out.append(
                    {
                        "order_id": row["order_id"],
                        "symbol": row.get("symbol"),
                        "side": row.get("side"),
                        "quantity": row.get("quantity"),
                        "rule": raw.get("rule") or row.get("signal_rule"),
                        "reason": raw.get("reason")
                        or raw.get("signal_reason")
                        or row.get("signal_reason"),
                        "price": raw.get("price"),
                        "strategy_id": row.get("strategy_id"),
                        "strategy_version": row.get("strategy_version"),
                        "ts": event.get("ts") or row.get("created_at"),
                    }
                )
        out.sort(key=lambda r: str(r.get("ts") or ""), reverse=True)
        return out

    # ------------------------------------------------------------------
    # trades
    # ------------------------------------------------------------------
    def trades(self, user_id: str, deployment_id: str, *, limit: int = 200) -> dict[str, Any]:
        """Closed and open trades from the journal.

        The journal is the only place a trade's *duration*, MFE and MAE are
        recorded — the order log holds fills, not episodes. So this read is
        honest about being empty when nothing has journalled yet, rather than
        reconstructing episodes from fills and presenting a guess as a record.
        """
        self._require(user_id, deployment_id)
        rows, total, open_rows, open_total = self._portfolio_storage.monitoring_journal_pages(
            user_id, deployment_id, limit
        )
        return {
            "trades": rows,
            "total": total,
            # An open trade has no realised P&L, so it is reported separately: a
            # win rate computed from a list that mixed the two would be dragged
            # toward zero by rows that have not resolved yet.
            "open": [r for r in open_rows if r.get("exit_ts") is None],
            "open_count": sum(1 for r in open_rows if r.get("exit_ts") is None),
            "all_count": open_total,
        }

    # ------------------------------------------------------------------
    # risk
    # ------------------------------------------------------------------
    def risk(self, user_id: str, deployment_id: str) -> dict[str, Any]:
        """The risk state as it applies to *this* deployment.

        The kill switch and the limits are platform-wide (they live in
        ``system_state``), so they are reported as-is alongside the deployment's
        own folded book — the view count, the exposure and the symbols — because
        a limit is only meaningful next to the number it is compared against.
        """
        self._require(user_id, deployment_id)
        snapshot = self.ledger.snapshot(user_id, deployment_id=deployment_id)
        return self._risk_from_snapshot(snapshot)

    def _risk_from_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        state = self._risk_state.snapshot()

        limits = state.limits
        if dataclasses.is_dataclass(limits) and not isinstance(limits, type):
            limits = _jsonable(limits)

        return {
            "kill_switch": state.kill_switch,
            "execution_mode": state.execution_mode,
            "limits": limits,
            "changed_at": state.changed_at,
            "changed_by": state.changed_by,
            "reason": state.reason,
            # The deployment's live numbers, so a limit can be read against the
            # figure it constrains rather than in the abstract.
            "observed": {
                "open_positions": len(snapshot["positions"]),
                "gross_exposure": snapshot["gross_exposure"],
                "net_exposure": snapshot["net_exposure"],
                "equity": snapshot["equity"],
                "cash": snapshot["cash"],
                "unpriced_symbols": snapshot["unpriced_symbols"],
            },
        }

    # ------------------------------------------------------------------
    # the timeline
    # ------------------------------------------------------------------
    def timeline(
        self, user_id: str, deployment_id: str, *, limit: int = 300
    ) -> list[TimelineEntry]:
        """Signal → Risk → Order → Fill → Position, as one ordered list.

        Built from ``order_events``, which is append-only and therefore the only
        source that can answer "what happened, in what order". Each entry carries
        the stage it belongs to so the UI can render the chain without inferring
        it from a status string, and the reason when the step was a decision.

        Position updates are emitted per fill, because that is when the position
        changes. The quantity shown is the cumulative filled quantity on that
        order, which is what the ledger had applied at that instant.
        """
        self._require(user_id, deployment_id)
        rows, by_order = _read_log(self._order_storage, user_id, deployment_id, limit=limit)
        return timeline_from_log(deployment_id, rows, by_order)

    # ------------------------------------------------------------------
    # the whole screen
    # ------------------------------------------------------------------
    def overview(
        self,
        user_id: str,
        deployment_id: str,
        *,
        prices: dict[str, float] | None = None,
        timeline_limit: int = 300,
    ) -> dict[str, Any]:
        """Everything the monitoring screen renders, in one read.

        One request rather than six, because the six views are of one state: a
        screen that fetches P&L, then positions, then orders can render a position
        from before a fill next to a P&L from after it, and the two disagree for
        as long as the operator is looking at them.
        """
        row = self._require(user_id, deployment_id)
        book = self.ledger.snapshot_and_fills(user_id, deployment_id=deployment_id, prices=prices)
        pnl = self.pnl(user_id, deployment_id, prices=prices, _book=book)
        trades = self.trades(user_id, deployment_id)
        status = self._status_from_row(
            user_id,
            deployment_id,
            row,
            pnl_info=pnl,
            trades_info=trades,
        )
        rows, by_order = _read_log(
            self._order_storage, user_id, deployment_id, limit=timeline_limit
        )
        return {
            "status": status,
            "pnl": pnl,
            "positions": pnl["positions"],
            "orders": self._orders_from_log(rows, by_order),
            "fills": self._fills_from_log(book[1]),
            "signals": self._signals_from_log(rows, by_order),
            "trades": trades,
            "risk": self._risk_from_snapshot(pnl),
            "timeline": [
                {
                    "ts": e.ts.isoformat(),
                    "stage": e.stage,
                    "symbol": e.symbol,
                    "summary": e.summary,
                    "outcome": e.outcome,
                    "reason": e.reason,
                    "order_id": e.order_id,
                    "side": e.side,
                    "detail": e.detail,
                }
                for e in timeline_from_log(deployment_id, rows, by_order)
            ],
        }
