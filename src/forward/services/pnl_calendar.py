"""Build daily P&L views for paper trades and the real holdings book."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any, Literal

import pandas as pd

from forward.core.insights_ports import InsightsHoldingsPort
from forward.core.paper_pnl_ports import PaperPnlStoragePort
from forward.core.pnl_ports import (
    PnlCalendarDataPort,
)
from forward.core.session import IST


def _market_today() -> date:
    """Return the current date in the Indian market timezone."""
    return datetime.now(IST).date()


@dataclass
class Day:
    """One calendar day's combined P&L."""

    date: str
    pnl: float
    trades: int = 0
    wins: int = 0
    losses: int = 0
    estimated: bool = False
    realised: float = 0.0


def _add(days: dict[str, Day], when: str, pnl: float) -> None:
    """Add one closed trade to the day totals."""
    day = days.setdefault(when, Day(date=when, pnl=0.0))
    day.pnl += pnl
    day.trades += 1
    day.wins += pnl > 0
    day.losses += pnl < 0


def paper_days(
    storage: PaperPnlStoragePort,
    user_id: str,
) -> dict[str, Day]:
    """Return one user's closed paper trades grouped by exit date.

    Args:
        storage: Adapter that reads the user's closed paper trades.
        user_id: Owner of the trades to include.
    """
    days: dict[str, Day] = {}
    for when, pnl in storage.closed_paper_trade_pnl(user_id):
        _add(days, when, pnl)
    return days


def gl_summary_data(source: PnlCalendarDataPort) -> dict[str, Any]:
    """Return the official broker gain and loss report."""
    return source.official_gl_summary()


def realised_entries(
    source: PnlCalendarDataPort,
    user_id: str,
) -> dict[str, dict[str, Any]]:
    """Return daily realized P&L, with manual entries taking precedence.

    Args:
        source: Port that reads official and manually entered P&L.
        user_id: Account that owns the manual entries.

    Returns:
        Entries keyed by ISO date.
    """
    entries: dict[str, dict[str, Any]] = {}
    gl = source.official_gl_summary()
    for row in gl.get("daily_pnl", {}).get("data") or []:
        raw_date = row.get("date")
        if not raw_date:
            continue
        day = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
        cash_pl = float(row.get("cashPl") or 0.0)
        fno_pl = float(row.get("fnoPl") or 0.0)
        dividend = float(row.get("dividend") or 0.0)
        brokerage = float(row.get("cashBrokerage") or 0.0) + float(row.get("fnoBrokerage") or 0.0)
        charges = float(row.get("cashChargesTaxes") or 0.0) + float(
            row.get("fnoChargesTaxes") or 0.0
        )
        net_pl = cash_pl + fno_pl + dividend - brokerage - charges
        entries[day] = {
            "amount": round(net_pl, 2),
            "source": "iifl_backoffice",
            "cash_pl": cash_pl,
            "fno_pl": fno_pl,
            "dividend": dividend,
            "brokerage": round(brokerage, 2),
            "charges": round(charges, 2),
            "note": "IIFL Official Realized GL",
            "updated": "synced",
        }

    entries.update(source.manual_realised_entries(user_id))
    return entries


def set_realised(
    data: PnlCalendarDataPort,
    user_id: str,
    day: str,
    amount: float,
    *,
    note: str = "",
    source: str = "manual",
) -> None:
    """Record or replace trading profit for one day.

    Args:
        data: Port that stores manually entered trading profit.
        user_id: Account that owns the entry.
        day: ISO market date.
        amount: Trading profit or loss in rupees.
        note: Short note shown with the entry.
        source: Name of the source that supplied the entry.

    Raises:
        ValueError: If the date is invalid or the amount is not finite.
    """
    date.fromisoformat(day)
    if not math.isfinite(amount):
        raise ValueError("amount must be a finite number")
    entries = data.manual_realised_entries(user_id)
    entries[day] = {
        "amount": round(float(amount), 2),
        "source": source,
        "note": note.strip()[:200],
        "updated": datetime.now().isoformat(timespec="seconds"),
    }
    data.save_manual_realised_entries(user_id, entries)


def clear_realised(source: PnlCalendarDataPort, user_id: str, day: str) -> bool:
    """Remove one daily trading profit entry.

    Args:
        source: Port that reads and stores manual entries.
        user_id: Account that owns the entry.
        day: ISO market date.

    Returns:
        Whether an entry was removed.
    """
    entries = source.manual_realised_entries(user_id)
    if day not in entries:
        return False
    del entries[day]
    source.save_manual_realised_entries(user_id, entries)
    return True


def _stored_days(source: PnlCalendarDataPort) -> dict[str, Day]:
    """Return stored holdings P&L without adding trading profit."""
    keep = ("date", "pnl", "trades", "wins", "losses", "estimated")
    return {
        key: Day(**{field: value[field] for field in keep if field in value})
        for key, value in source.stored_pnl_days().items()
    }


def real_days(source: PnlCalendarDataPort, user_id: str) -> dict[str, Day]:
    """Return daily holdings changes plus recorded trading profit."""
    days = _stored_days(source)
    for when, entry in realised_entries(source, user_id).items():
        amount = float(entry["amount"])
        day = days.get(when)
        if day is None:
            days[when] = Day(date=when, pnl=amount, trades=0, realised=amount)
        else:
            day.pnl, day.realised = round(day.pnl + amount, 2), amount
    return days


def _closes(source: PnlCalendarDataPort, symbol: str) -> pd.Series | None:
    """Read cached daily closes through the data port."""
    return source.daily_closes(symbol)


def _daily_change(holdings: list[dict[str, Any]], closes: dict[str, pd.Series]) -> pd.DataFrame:
    """Return each holding's change from the prior session, by day."""
    symbols = [holding["symbol"] for holding in holdings]
    if not any(symbol in closes for symbol in symbols):
        return pd.DataFrame()
    prices = pd.concat(
        {
            symbol: closes[symbol]
            for symbol in symbols
            if symbol in closes
        },
        axis=1,
    ).sort_index()
    for symbol in symbols:
        if symbol not in prices:
            prices[symbol] = float("nan")
    return prices[symbols].diff()


def compute_real(
    holdings: list[dict[str, Any]],
    closes: dict[str, pd.Series],
    *,
    sessions: int,
) -> dict[str, float]:
    """Calculate daily changes in the value of the current holdings.

    Args:
        holdings: Current symbols and quantities.
        closes: Daily close series keyed by symbol.
        sessions: Maximum number of sessions to return.

    Returns:
        Rupee P&L keyed by ISO market date.
    """
    change = _daily_change(holdings, closes)
    if change.empty:
        return {}
    quantities = pd.Series({holding["symbol"]: holding["qty"] for holding in holdings})
    output: dict[str, float] = {}
    for when, row in change.tail(sessions).iterrows():
        if row.isna().any():
            continue
        output[when.date().isoformat()] = float((row * quantities[row.index]).sum())
    return output


def refresh_real(
    source: PnlCalendarDataPort,
    holdings: list[dict[str, Any]],
    *,
    sessions: int,
    today: date | None = None,
) -> int:
    """Save the daily holdings change without mixing in trading profit.

    Args:
        source: Port that reads cached prices and stores daily P&L.
        holdings: Current symbols and quantities.
        today: Market date used to mark the exact current-day value.

    Returns:
        Number of daily records changed.
    """
    if not holdings:
        return 0
    closes = {
        holding["symbol"]: series
        for holding in holdings
        if (series := _closes(source, holding["symbol"])) is not None
    }
    computed = compute_real(holdings, closes, sessions=sessions)
    if not computed:
        return 0
    days = _stored_days(source)
    last_session = max(computed)
    today_iso = (today or _market_today()).isoformat()
    changed = 0
    for when, pnl in computed.items():
        exact = when == last_session == today_iso
        old = days.get(when)
        if old is not None and not old.estimated:
            continue
        days[when] = Day(
            date=when,
            pnl=round(pnl, 2),
            trades=len(holdings),
            estimated=not exact,
        )
        changed += 1
    source.save_pnl_days(
        {key: asdict(day) for key, day in sorted(days.items())},
        datetime.now().isoformat(timespec="seconds"),
    )
    return changed


def unpriced_holdings(source: PnlCalendarDataPort, holdings: list[dict[str, Any]]) -> list[str]:
    """Return holdings that have no local daily price history."""
    return [holding["symbol"] for holding in holdings if _closes(source, holding["symbol"]) is None]


def paper_day_detail(
    user_id: str,
    day: str,
    storage: PaperPnlStoragePort,
) -> list[dict[str, Any]]:
    """Return one user's paper trades closed on one day.

    Args:
        user_id: Owner of the trades to include.
        day: ISO calendar date.
        storage: Adapter that reads closed paper trades.
    """
    rows = storage.closed_paper_trades_on(user_id, day)
    return sorted(rows, key=lambda row: -abs(row["pnl"]))


def real_day_detail(
    source: PnlCalendarDataPort,
    holdings: list[dict[str, Any]],
    day: str,
    user_id: str,
) -> dict[str, Any]:
    """Explain which holdings changed the real P&L for one day."""
    closes = {
        holding["symbol"]: series
        for holding in holdings
        if (series := _closes(source, holding["symbol"])) is not None
    }
    change = _daily_change(holdings, closes)
    when = pd.Timestamp(day)
    rows: list[dict[str, Any]] = []
    priced: set[str] = set()
    if not change.empty and when in change.index:
        index = change.index.get_loc(when)
        for holding in holdings:
            symbol = holding["symbol"]
            delta = change[symbol].iloc[index] if symbol in change.columns else float("nan")
            if pd.isna(delta):
                continue
            series = closes[symbol]
            close = float(series.loc[when])
            previous = close - float(delta)
            priced.add(symbol)
            rows.append(
                {
                    "symbol": symbol,
                    "quantity": holding["qty"],
                    "previous_close": round(previous, 2),
                    "close": round(close, 2),
                    "change_pct": round(100 * float(delta) / previous, 2),
                    "pnl": round(holding["qty"] * float(delta), 2),
                }
            )
    rows.sort(key=lambda row: -abs(row["pnl"]))
    complete = len(priced) == len(holdings)
    holdings_total = round(sum(row["pnl"] for row in rows), 2) if complete else None
    trading = realised_entries(source, user_id).get(day)
    return {
        "rows": rows,
        "holdings_total": holdings_total,
        "realised": trading,
        "total": (
            round(holdings_total + (trading["amount"] if trading else 0.0), 2)
            if holdings_total is not None
            else None
        ),
        "gainers": sum(1 for row in rows if row["pnl"] > 0),
        "losers": sum(1 for row in rows if row["pnl"] < 0),
        "unpriced": [holding["symbol"] for holding in holdings if holding["symbol"] not in priced],
    }


def _runs(days: list[Day]) -> dict[str, int]:
    """Count consecutive profitable and losing days across all history."""
    best_green = worst_red = green = red = 0
    for day in days:
        green = green + 1 if day.pnl > 0 else 0
        red = red + 1 if day.pnl < 0 else 0
        best_green = max(best_green, green)
        worst_red = max(worst_red, red)
    return {
        "current_green": green,
        "best_green": best_green,
        "worst_red": worst_red,
    }


def month_view(days: dict[str, Day], month: str) -> dict[str, Any]:
    """Build one month's calendar and summary figures."""
    ordered = [days[key] for key in sorted(days)]
    in_month = [day for day in ordered if day.date.startswith(month)]
    best = max(in_month, key=lambda day: day.pnl) if in_month else None
    worst = min(in_month, key=lambda day: day.pnl) if in_month else None
    return {
        "month": month,
        "days": [asdict(day) for day in in_month],
        "total": round(sum(day.pnl for day in in_month), 2),
        "traded_days": len(in_month),
        "green_days": sum(1 for day in in_month if day.pnl > 0),
        "red_days": sum(1 for day in in_month if day.pnl < 0),
        "best_day": ({"date": best.date, "pnl": round(best.pnl, 2)} if best else None),
        "worst_day": ({"date": worst.date, "pnl": round(worst.pnl, 2)} if worst else None),
        **_runs(ordered),
        "months_with_data": sorted({day.date[:7] for day in ordered}),
        "any_estimated": any(day.estimated for day in in_month),
    }


class PnlCalendarInputError(ValueError):
    """A P&L calendar request contains an invalid date or future date."""


class PnlCalendarService:
    """Build P&L calendar views from paper, holdings, and realized data ports."""

    def __init__(
        self,
        *,
        data: PnlCalendarDataPort,
        paper_storage: PaperPnlStoragePort,
        holdings: InsightsHoldingsPort,
    ) -> None:
        """Create the calendar use case.

        Args:
            data: Local data for calendar records and cached prices.
            paper_storage: Closed paper trades stored in the application database.
            holdings: Current real holdings and their data source.
        """
        self._data = data
        self._paper_storage = paper_storage
        self._holdings = holdings

    def month(
        self, scope: Literal["paper", "real"], month: str | None, user_id: str
    ) -> dict[str, Any]:
        """Return one month's P&L calendar and summary.

        Args:
            scope: Paper trades or real holdings.
            month: Requested year and month, or ``None`` for the latest month.
            user_id: Signed-in user whose paper trades are shown.

        Returns:
            Calendar days and summary fields for the requested month.

        Raises:
            PnlCalendarInputError: If the month is not a real calendar month.
        """
        days = (
            paper_days(self._paper_storage, user_id)
            if scope == "paper"
            else real_days(self._data, user_id)
        )
        if month is None:
            month = max((day[:7] for day in days), default=_market_today().strftime("%Y-%m"))
        try:
            date.fromisoformat(f"{month}-01")
        except ValueError as error:
            raise PnlCalendarInputError("month must be a real YYYY-MM") from error

        view = month_view(days, month)
        view["scope"] = scope
        if scope == "real":
            held = self._holdings.load(source="SNAPSHOT")
            view["holdings_source"] = held["source"]
            view["unpriced"] = unpriced_holdings(self._data, held["holdings"])
            gl = gl_summary_data(self._data)
            if gl and gl.get("summary", {}).get("data"):
                view["tax_gl_summary"] = gl["summary"]["data"]
        return view

    def day(self, scope: Literal["paper", "real"], day: str, user_id: str) -> dict[str, Any]:
        """Return the trade or holdings detail for one day.

        Args:
            scope: Paper trades or real holdings.
            day: ISO market date.
            user_id: Signed-in user whose paper trades are shown.

        Returns:
            Daily P&L and its supporting rows.

        Raises:
            PnlCalendarInputError: If the date is not a real calendar date.
        """
        try:
            date.fromisoformat(day)
        except ValueError as error:
            raise PnlCalendarInputError("date must be a real YYYY-MM-DD") from error

        if scope == "paper":
            rows = paper_day_detail(user_id, day, self._paper_storage)
            return {
                "scope": scope,
                "date": day,
                "rows": rows,
                "total": round(sum(row["pnl"] for row in rows), 2),
                "gainers": sum(1 for row in rows if row["pnl"] > 0),
                "losers": sum(1 for row in rows if row["pnl"] < 0),
                "unpriced": [],
            }

        held = self._holdings.load(source="SNAPSHOT")
        detail = real_day_detail(self._data, held["holdings"], day, user_id)
        return {
            "scope": scope,
            "date": day,
            **detail,
            "holdings_source": held["source"],
        }

    def record_realised(
        self, user_id: str, day: str, amount: float, note: str
    ) -> dict[str, Any]:
        """Record a manually entered result for a completed market day.

        Args:
            day: ISO market date.
            user_id: Account that owns the entry.
            amount: Profit or loss in rupees.
            note: Short note saved with the result.

        Returns:
            The saved date and amount.

        Raises:
            PnlCalendarInputError: If the date is invalid or in the future.
        """
        try:
            parsed_day = date.fromisoformat(day)
        except ValueError as error:
            raise PnlCalendarInputError("date must be a real YYYY-MM-DD") from error
        if parsed_day > _market_today():
            raise PnlCalendarInputError("that day has not happened yet")
        set_realised(self._data, user_id, day, amount, note=note)
        return {"date": day, "amount": amount}

    def remove_realised(self, user_id: str, day: str) -> dict[str, bool]:
        """Remove a manually entered daily result.

        Args:
            day: ISO market date supplied by the route.
            user_id: Account that owns the entry.

        Returns:
            Whether the stored result was removed.
        """
        return {"removed": clear_realised(self._data, user_id, day)}
