"""The track record: what a paper deployment actually did, day by day.

The paper ledger is event-sourced: there is no table of daily equity, only the fills that
produced the account. So a track record is a *replay*. For each trading day we apply every
fill up to the close of that day and value what is still held at that day's close.

Three things are kept honest, because this is the page a person judges the product on:

* **Nothing is invented.** A position with no close on or before a day is valued at its cost
  and the day is flagged ``priced = False``. The response says how many days were affected
  rather than smoothing them over.
* **Unknown costs stay unknown.** A curve is called net only when every fill has a known
  commission. Otherwise the response exposes the pre-unknown-fee curve and marks net P&L
  incomplete.
* **It says what it is.** ``provenance`` states that fills are *simulated* by the paper
  venue, where prices came from, and how recent they are. A simulated curve is evidence about
  a strategy's logic, not a promise about real fills.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from forward.config.settings import get_settings
from forward.core.calendar_ports import MarketCalendarPort
from forward.core.market_tracking_ports import TrackRecordMarketDataPort
from forward.core.order_models import Fill
from forward.core.paper_ledger_ports import PaperFillLedgerPort
from forward.core.portfolio_ports import (
    PortfolioStoragePort,
)
from forward.core.session import IST
from forward.execution.portfolio import Portfolio

#: ``(symbol, exchange, day) -> that day's close``, or None when it is missing.
CloseOn = Callable[[str, str, date], float | None]


@dataclass(frozen=True)
class DayPoint:
    day: date
    equity: float | None
    equity_before_unknown_fees: float
    #: False when a held position had to be valued at cost for lack of a close.
    priced: bool


def fill_day(fill: Fill) -> date:
    """The IST trading date a fill belongs to. A naive timestamp is taken as UTC."""
    ts = fill.ts if fill.ts.tzinfo else fill.ts.replace(tzinfo=UTC)
    return ts.astimezone(IST).date()


def replay_equity(
    fills: list[Fill], initial_cash: float, days: list[date], close_on: CloseOn
) -> tuple[list[DayPoint], dict[str, Any]]:
    """Equity at the close of each of ``days``, replaying ``fills`` oldest first.

    Also returns the closed-trade statistics from the same replay. A trade is an *episode*: it opens
    when a position leaves flat and closes when it returns to flat. Its net result is measured only
    when every fee in the replay is known, matching the trade journal's completeness rule.
    """
    ordered = sorted(fills, key=lambda f: f.ts)
    portfolio = Portfolio(initial_cash=initial_cash)
    out: list[DayPoint] = []
    closed = wins = losses = 0
    gross_win = gross_loss = 0.0
    episode: dict[str, list[float]] = {}  # symbol -> [realised, known commission]
    i = 0
    for day in days:
        while i < len(ordered) and fill_day(ordered[i]) <= day:
            fill = ordered[i]
            symbol = fill.instrument.symbol
            held = portfolio.positions.get(symbol)
            was_flat = held is None or held.is_flat
            realised = portfolio.apply_fill(fill)
            acc = episode.setdefault(symbol, [0.0, 0.0])
            acc[0] += realised
            if fill.commission is not None:
                acc[1] += abs(fill.commission)
            if not was_flat and portfolio.positions[symbol].is_flat:
                closed += 1
                if portfolio.commission_complete:
                    net = acc[0] - acc[1]
                    if net > 0:
                        wins += 1
                        gross_win += net
                    elif net < 0:
                        losses += 1
                        gross_loss += -net
                episode.pop(symbol)
            i += 1
        priced = True
        value = portfolio.cash
        for position in portfolio.positions.values():
            if position.is_flat:
                continue
            symbol = position.instrument.symbol
            price = close_on(symbol, position.instrument.exchange, day)
            if price is None:
                price, priced = position.avg_price, False
            value += position.quantity * price * position.instrument.multiplier
        out.append(
            DayPoint(
                day=day,
                equity=value if portfolio.commission_complete and priced else None,
                equity_before_unknown_fees=value,
                priced=priced,
            )
        )
    stats = {
        "closed_trades": closed,
        "wins": wins,
        "losses": losses,
        "gross_win": gross_win,
        "gross_loss": gross_loss,
        "commission_paid": (
            portfolio.commission_paid if portfolio.commission_complete else None
        ),
        "commission_complete": portfolio.commission_complete,
    }
    return out, stats


def _payoff(wins: int, losses: int, gross_win: float, gross_loss: float) -> dict[str, Any]:
    """Average win, average loss and profit factor, each None when it cannot be measured.

    A win rate alone says nothing: 53% of trades can win and the book still lose money. The
    payoff is what turns it into an expectancy. Profit factor needs a loss to divide by.
    """
    return {
        "win_count": wins,
        "loss_count": losses,
        "gross_win": gross_win,
        "gross_loss": gross_loss,
        "avg_win": gross_win / wins if wins else None,
        "avg_loss": -gross_loss / losses if losses else None,
        "profit_factor": gross_win / gross_loss if gross_loss > 0 else None,
    }


def max_drawdown_pct(values: list[float]) -> float:
    """The deepest peak-to-trough fall, as a negative percent (0.0 when it never fell)."""
    peak = worst = 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            worst = min(worst, (v / peak - 1.0) * 100.0)
    return worst


def current_drawdown_pct(values: list[float]) -> float:
    if not values:
        return 0.0
    peak = max(values)
    return (values[-1] / peak - 1.0) * 100.0 if peak > 0 else 0.0


def build_record(
    *,
    fills: list[Fill],
    initial_cash: float,
    calendar: list[date],
    close_on: CloseOn,
    benchmark: dict[date, float] | None,
    window_days: int,
    today: date,
) -> dict[str, Any]:
    """Equity, benchmark and summary for one account, over the last ``window_days``."""
    if not calendar or initial_cash <= 0:
        return {"has_data": False}
    points, stats = replay_equity(fills, initial_cash, calendar, close_on)

    cutoff = today - timedelta(days=window_days)
    shown = [p for p in points if p.day >= cutoff] or points[-1:]
    # Return is measured from the capital when the whole history fits the window, and from the
    # first shown day when older history was cut off, so the figure matches the chart.
    base = (
        initial_cash
        if shown[0].day == points[0].day
        else shown[0].equity_before_unknown_fees
    )
    before_unknown_fees = [p.equity_before_unknown_fees for p in shown]
    pnl_complete = stats["commission_complete"] and all(p.priced for p in shown)

    bench_rows: list[float | None] = []
    bench_base: float | None = None
    if benchmark:
        bench_base = benchmark.get(shown[0].day)
        for p in shown:
            b = benchmark.get(p.day)
            bench_rows.append(None if b is None or not bench_base else base * b / bench_base)
    else:
        bench_rows = [None] * len(shown)

    ret = (before_unknown_fees[-1] / base - 1.0) * 100.0 if base else 0.0
    bench_vals = [b for b in bench_rows if b is not None]
    bench_ret = (bench_vals[-1] / base - 1.0) * 100.0 if bench_vals and base else None
    closed = stats["closed_trades"]
    return {
        "has_data": True,
        "capital": initial_cash,
        "base": base,
        "start": shown[0].day.isoformat(),
        "end": shown[-1].day.isoformat(),
        "trading_days": len(shown),
        "points": [
            {
                "d": p.day.isoformat(),
                "equity": round(p.equity, 2) if pnl_complete else None,
                "equity_before_unknown_fees": round(p.equity_before_unknown_fees, 2),
                "benchmark": None if b is None else round(b, 2),
            }
            for p, b in zip(shown, bench_rows, strict=True)
        ],
        "summary": {
            "return_pct": ret if pnl_complete else None,
            "benchmark_return_pct": bench_ret,
            "excess_pct": (
                None if bench_ret is None or not pnl_complete else ret - bench_ret
            ),
            "max_drawdown_pct": (
                max_drawdown_pct([base, *before_unknown_fees]) if pnl_complete else None
            ),
            "current_drawdown_pct": (
                current_drawdown_pct([base, *before_unknown_fees]) if pnl_complete else None
            ),
            "net_pnl": before_unknown_fees[-1] - base if pnl_complete else None,
            "closed_trades": closed,
            "win_rate_pct": (
                stats["wins"] / closed * 100.0 if pnl_complete and closed else None
            ),
            "commission_paid": stats["commission_paid"],
            **(
                _payoff(stats["wins"], stats["losses"], stats["gross_win"], stats["gross_loss"])
                if pnl_complete
                else {
                    "win_count": None,
                    "loss_count": None,
                    "gross_win": None,
                    "gross_loss": None,
                    "avg_win": None,
                    "avg_loss": None,
                    "profit_factor": None,
                }
            ),
        },
        "pnl_complete": pnl_complete,
        "commission_complete": stats["commission_complete"],
        "unpriced_days": sum(1 for p in shown if not p.priced),
    }


def combine(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Add several accounts into one: the whole paper book as a single curve."""
    live = [r for r in records if r.get("has_data")]
    if not live:
        return {"has_data": False}
    if len(live) == 1:
        return live[0]
    days = sorted({p["d"] for r in live for p in r["points"]})
    by_rec = [{p["d"]: p for p in r["points"]} for r in live]
    carry: list[dict[str, Any] | None] = [None] * len(live)
    points: list[dict[str, Any]] = []
    pnl_complete = all(record.get("pnl_complete", False) for record in live)
    commission_complete = all(record.get("commission_complete", False) for record in live)
    for d in days:
        eq = 0.0
        bench: float | None = 0.0
        for k, rec in enumerate(by_rec):
            if d in rec:
                carry[k] = rec[d]
            row = carry[k]
            # Before an account's first shown day it was still just its starting capital.
            eq += row["equity_before_unknown_fees"] if row else live[k]["base"]
            if row is None:
                bench = bench + live[k]["base"] if bench is not None else None
            elif row["benchmark"] is None:
                bench = None
            elif bench is not None:
                bench += row["benchmark"]
        points.append(
            {
                "d": d,
                "equity": round(eq, 2) if pnl_complete else None,
                "equity_before_unknown_fees": round(eq, 2),
                "benchmark": None if bench is None else round(bench, 2),
            }
        )
    base = sum(r["base"] for r in live)
    values = [p["equity_before_unknown_fees"] for p in points]
    bench_vals = [p["benchmark"] for p in points if p["benchmark"] is not None]
    ret = (values[-1] / base - 1.0) * 100.0 if base else 0.0
    bench_ret = (bench_vals[-1] / base - 1.0) * 100.0 if bench_vals else None
    closed = sum(r["summary"]["closed_trades"] for r in live)
    wins = sum(r["summary"]["win_count"] or 0 for r in live)
    losses = sum(r["summary"]["loss_count"] or 0 for r in live)
    gross_win = sum(r["summary"]["gross_win"] or 0.0 for r in live)
    gross_loss = sum(r["summary"]["gross_loss"] or 0.0 for r in live)
    return {
        "has_data": True,
        "capital": sum(r["capital"] for r in live),
        "base": base,
        "start": days[0],
        "end": days[-1],
        "trading_days": len(days),
        "points": points,
        "summary": {
            "return_pct": ret if pnl_complete else None,
            "benchmark_return_pct": bench_ret,
            "excess_pct": (
                None if bench_ret is None or not pnl_complete else ret - bench_ret
            ),
            "max_drawdown_pct": (
                max_drawdown_pct([base, *values]) if pnl_complete else None
            ),
            "current_drawdown_pct": (
                current_drawdown_pct([base, *values]) if pnl_complete else None
            ),
            "net_pnl": values[-1] - base if pnl_complete else None,
            "closed_trades": closed,
            "win_rate_pct": (
                wins / closed * 100.0 if pnl_complete and closed else None
            ),
            "commission_paid": (
                sum(r["summary"]["commission_paid"] for r in live)
                if commission_complete
                else None
            ),
            **(
                _payoff(wins, losses, gross_win, gross_loss)
                if pnl_complete
                else {
                    "win_count": None,
                    "loss_count": None,
                    "gross_win": None,
                    "gross_loss": None,
                    "avg_win": None,
                    "avg_loss": None,
                    "profit_factor": None,
                }
            ),
        },
        "pnl_complete": pnl_complete,
        "commission_complete": commission_complete,
        "unpriced_days": max(r["unpriced_days"] for r in live),
    }


# ───────────────────────────── data access ─────────────────────────────


class _CloseByDate:
    """Look up a close for one exact market date."""

    def __init__(self, closes: dict[date, float]) -> None:
        self._days = sorted(closes)
        self._closes = closes

    def __call__(self, day: date) -> float | None:
        return self._closes.get(day)

    @property
    def last_day(self) -> date | None:
        return self._days[-1] if self._days else None


@dataclass
class TrackRecordService:
    """Build track records from the paper ledger and market-data port."""

    storage: PortfolioStoragePort
    ledger: PaperFillLedgerPort
    market_data: TrackRecordMarketDataPort
    calendar: MarketCalendarPort
    today: Callable[[], date] | None = None

    # -- prices ---------------------------------------------------------
    def _stock_closes(self) -> tuple[CloseOn, Callable[[], date | None]]:
        cache: dict[tuple[str, str], _CloseByDate | None] = {}
        latest: list[date] = []

        def lookup(symbol: str, exchange: str) -> _CloseByDate | None:
            key = (symbol, exchange)
            if key not in cache:
                closes = self.market_data.stock_closes(symbol, exchange)
                series = _CloseByDate(closes) if closes else None
                if series and series.last_day:
                    latest.append(series.last_day)
                cache[key] = series
            return cache[key]

        def close_on(symbol: str, exchange: str, day: date) -> float | None:
            series = lookup(symbol, exchange)
            return series(day) if series else None

        return close_on, lambda: max(latest) if latest else None

    def _is_demo(self) -> bool:
        return self.market_data.is_demo()

    def _benchmark(self) -> tuple[dict[date, float] | None, dict[str, Any]]:
        return self.market_data.benchmark()

    # -- records --------------------------------------------------------
    def _record_for(
        self,
        row: dict[str, Any],
        user_id: str,
        window_days: int,
        close_on: CloseOn,
        benchmark: dict[date, float] | None,
        calendar: list[date],
        today: date,
    ) -> dict[str, Any]:
        fills = self.ledger.fills(user_id, deployment_id=row["deployment_id"])
        capital = float(row["capital"])
        first = min([fill_day(f) for f in fills], default=None)
        if first is None:
            return {"has_data": False}
        days = [d for d in calendar if d >= first]
        return build_record(
            fills=fills,
            initial_cash=capital,
            calendar=days,
            close_on=close_on,
            benchmark=benchmark,
            window_days=window_days,
            today=today,
        )

    def _calendar(self, since: date, today: date) -> list[date]:
        """Return exchange sessions from the first fill through today.

        Args:
            since: Date of the first paper fill.
            today: Last date to include.

        Returns:
            Trading dates from the injected exchange calendar.
        """
        return [
            since + timedelta(days=offset)
            for offset in range((today - since).days + 1)
            if self.calendar.is_trading_day(since + timedelta(days=offset)) or offset == 0
        ]

    def journal_status(self, user_id: str) -> dict[str, Any]:
        """Whether the trade journal agrees with the ledger about how many trades have closed.

        Performance pages read the journal; the track record replays the ledger. They are meant to
        be the same arithmetic (the journal is a projection of the position fold), but the journal
        is written by a reconcile step, so it can lag. This says whether it does.
        """
        ledger_closed = journal_closed = 0
        rows = [
            row
            for row in self.storage.deployments(user_id)
            if str(row.get("mode", "")).upper() == "PAPER" and row.get("status") != "STOPPED"
        ]
        for row in rows:
            fills = self.ledger.fills(user_id, deployment_id=row["deployment_id"])
            if not fills:
                continue
            _, stats = replay_equity(
                fills, float(row["capital"]), [fill_day(fills[-1])], lambda *_: None
            )
            ledger_closed += stats["closed_trades"]
            journal_closed += self.storage.journal_episode_count(user_id, row["deployment_id"])
        return {
            "ledger_closed": ledger_closed,
            "journal_closed": journal_closed,
            "in_sync": ledger_closed == journal_closed,
        }

    def build(
        self, user_id: str, *, days: int, deployment_id: str | None = None
    ) -> dict[str, Any]:
        if days < 1 or days > get_settings().track_record_max_days:
            raise ValueError("days is outside the configured track-record window")
        today = self.today() if self.today else datetime.now(IST).date()
        if deployment_id:
            row = self.storage.deployment(user_id, deployment_id)
            rows = [row] if row else []
        else:
            deployments = self.storage.deployments(user_id)
            rows = [
                row
                for row in deployments
                if str(row.get("mode", "")).upper() == "PAPER" and row.get("started_at")
            ]
        close_on, stocks_latest = self._stock_closes()
        benchmark, bench_meta = self._benchmark()

        records: list[dict[str, Any]] = []
        for row in rows:
            fills_first = self.ledger.fills(user_id, deployment_id=row["deployment_id"])
            if not fills_first:
                continue
            since = min(fill_day(f) for f in fills_first)
            calendar = self._calendar(since, today)
            records.append(
                self._record_for(row, user_id, days, close_on, benchmark, calendar, today)
            )
        record = combine(records)

        stocks_as_of = stocks_latest()
        return {
            "scope": "deployment" if deployment_id else "all",
            "deployment_id": deployment_id,
            "window_days": days,
            "deployments": len(records),
            **record,
            "provenance": {
                "simulated": True,
                "demo": self._is_demo(),
                "fills": "Simulated by the paper venue against recorded prices, with modelled slippage and brokerage. No real order was sent.",
                "prices": "Daily closes from the local cache (broker history, topped up from public bars).",
                "prices_as_of": stocks_as_of.isoformat() if stocks_as_of else None,
                "benchmark": bench_meta,
                "complete": record.get("unpriced_days", 0) == 0 if record.get("has_data") else True,
                "unpriced_days": record.get("unpriced_days", 0),
            },
        }
