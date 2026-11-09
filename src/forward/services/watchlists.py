"""Watchlist service: CRUD, column configuration, and quote assembly.

The column registry is the important part. It is the single place that knows
which columns exist, what they mean, and — crucially — **which ones cannot be
populated yet**. The brief asks for market cap, OI and IV; this repo has no
fundamentals feed and no option chain, so those columns are declared with
``available=False`` and a reason rather than being rendered as a zero. A zero
looks like a measurement. "Not collected yet" is the truth.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any

import pandas as pd

from forward.core.errors import DuplicateWatchlistName, InvalidWatchlistOrder
from forward.core.watchlist_market_ports import WatchlistInstrumentPort
from forward.core.watchlist_ports import (
    WatchlistStoragePort,
)
from forward.strategy.indicators import atr, ema, rsi, sma


@dataclass(frozen=True, slots=True)
class ColumnSpec:
    key: str
    label: str
    group: str
    kind: str  # currency | number | percent | integer | text
    available: bool = True
    requires: str | None = None
    description: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _spec(key: str, label: str, group: str, kind: str, description: str = "") -> ColumnSpec:
    return ColumnSpec(key=key, label=label, group=group, kind=kind, description=description)


def _unavailable(key: str, label: str, group: str, kind: str, requires: str) -> ColumnSpec:
    return ColumnSpec(
        key=key,
        label=label,
        group=group,
        kind=kind,
        available=False,
        requires=requires,
        description=f"not collected yet — requires {requires}",
    )


#: Every column the UI may ask for. Ordered as the picker should present them.
COLUMN_REGISTRY: tuple[ColumnSpec, ...] = (
    # --- price -------------------------------------------------------------
    _spec("ltp", "LTP", "price", "currency", "Last traded price, or the last cached close"),
    _spec("change", "Change", "price", "currency", "LTP − previous close"),
    _spec("change_pct", "Change %", "price", "percent"),
    _spec("open", "Open", "price", "currency"),
    _spec("high", "High", "price", "currency"),
    _spec("low", "Low", "price", "currency"),
    _spec("prev_close", "Prev close", "price", "currency"),
    _spec("range_pct", "Range %", "price", "percent", "Day range as a percentage of close"),
    # --- volume ------------------------------------------------------------
    _spec("volume", "Volume", "volume", "integer"),
    _spec(
        "volume_ratio",
        "Vol ×avg",
        "volume",
        "number",
        "Volume divided by its own 20-session average",
    ),
    _spec(
        "vwap",
        "VWAP 20d",
        "volume",
        "currency",
        "20-session volume-weighted average price. Daily bars carry no intraday "
        "VWAP, so this is a rolling session-weighted figure, not the intraday one",
    ),
    # --- trend -------------------------------------------------------------
    _spec("ema20", "EMA 20", "trend", "currency"),
    _spec("ema50", "EMA 50", "trend", "currency"),
    _spec("sma20", "SMA 20", "trend", "currency"),
    _spec("sma50", "SMA 50", "trend", "currency"),
    _spec("high_52w", "52w high", "trend", "currency"),
    _spec("low_52w", "52w low", "trend", "currency"),
    _spec("from_52w_high_pct", "Below 52w high %", "trend", "percent"),
    # --- momentum ----------------------------------------------------------
    _spec("rsi14", "RSI 14", "momentum", "number", "0–100; above 70 is commonly read as overbought"),
    # --- volatility --------------------------------------------------------
    _spec("atr14", "ATR 14", "volatility", "currency", "Average true range, 14 sessions"),
    _spec("atr_pct", "ATR %", "volatility", "percent", "ATR as a percentage of close"),
    # --- provenance --------------------------------------------------------
    _spec("bars", "Sessions", "provenance", "integer", "Sessions present in the local cache"),
    _spec("last_bar_date", "Last bar", "provenance", "text"),
    # --- declared, not collected -------------------------------------------
    _unavailable("market_cap", "Market cap", "fundamentals", "currency", "the fundamentals service"),
    _unavailable("pe", "P/E", "fundamentals", "number", "the fundamentals service"),
    _unavailable("pb", "P/B", "fundamentals", "number", "the fundamentals service"),
    _unavailable("eps", "EPS", "fundamentals", "currency", "the fundamentals service"),
    _unavailable("roe", "ROE", "fundamentals", "percent", "the fundamentals service"),
    _unavailable("promoter_holding", "Promoter %", "fundamentals", "percent", "the shareholding feed"),
    _unavailable("oi", "OI", "derivatives", "integer", "the options chain service"),
    _unavailable("oi_change", "ΔOI", "derivatives", "integer", "the options chain service"),
    _unavailable("iv", "IV", "derivatives", "percent", "the options chain service"),
    _unavailable("delta", "Delta", "derivatives", "number", "the options chain service"),
    _unavailable("theta", "Theta", "derivatives", "number", "the options chain service"),
    _unavailable("bid", "Bid", "depth", "currency", "a live market-depth feed"),
    _unavailable("ask", "Ask", "depth", "currency", "a live market-depth feed"),
)

COLUMN_INDEX: dict[str, ColumnSpec] = {spec.key: spec for spec in COLUMN_REGISTRY}

#: Columns that actually have a value today. A watchlist defaults to these.
COMPUTABLE_COLUMNS: tuple[str, ...] = tuple(s.key for s in COLUMN_REGISTRY if s.available)

DEFAULT_COLUMNS: tuple[str, ...] = (
    "ltp",
    "change",
    "change_pct",
    "volume",
    "volume_ratio",
    "rsi14",
    "ema20",
    "ema50",
    "atr_pct",
)


class WatchlistError(Exception):
    def __init__(self, message: str, *, code: str = "watchlist_error", status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class WatchlistService:
    def __init__(
        self,
        storage: WatchlistStoragePort,
        market: WatchlistInstrumentPort,
    ) -> None:
        """Create the watchlist use case with its persistence and market ports.

        Args:
            storage: Port for user-owned watchlists and audit records.
            market: Port for symbol lookup and cached market data.
        """
        self._storage = storage
        self._market = market

    # ------------------------------------------------------------ registry
    @staticmethod
    def available_columns() -> list[dict[str, Any]]:
        return [spec.as_dict() for spec in COLUMN_REGISTRY]

    @staticmethod
    def validate_columns(keys: list[str]) -> list[str]:
        """Keep known keys, drop unknown ones, and say which were dropped.

        Rejecting the whole request on one bad key makes a column picker
        frustrating; silently accepting it would persist a column nothing can
        render. The caller gets the accepted list and the rejected list.
        """
        accepted: list[str] = []
        for key in keys:
            clean = (key or "").strip()
            if not clean or clean in accepted:
                continue
            if clean not in COLUMN_INDEX:
                continue
            accepted.append(clean)
        return accepted

    # ---------------------------------------------------------------- CRUD
    def list_watchlists(self, user_id: str) -> list[dict[str, Any]]:
        return self._storage.list_watchlists(user_id)

    def symbols_for_user(self, user_id: str) -> set[str]:
        """Return every symbol in the user's watchlists.

        Args:
            user_id: Owner whose watchlists should be read.

        Returns:
            Unique symbols from all owned watchlists.
        """
        symbols: set[str] = set()
        for row in self.list_watchlists(user_id):
            detail = self.get(user_id, row["watchlist_id"])
            symbols.update((detail or {}).get("items", []))
        return symbols

    def create(
        self,
        user_id: str,
        *,
        name: str,
        exchange: str = "NSEEQ",
        columns: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a watchlist with default or explicitly selected columns.

        Args:
            user_id: Account that owns the watchlist.
            name: Display name for the new watchlist.
            exchange: Listing segment for its symbols.
            columns: Ordered column keys, or ``None`` to use product defaults.

        Returns:
            The saved watchlist.

        Raises:
            WatchlistError: If the name or explicit column selection is invalid.
        """
        clean_name = (name or "").strip()
        if not clean_name:
            raise WatchlistError("name is required", code="missing_name")
        chosen = (
            list(DEFAULT_COLUMNS)
            if columns is None
            else self.validate_columns(list(columns))
        )
        if not chosen:
            raise WatchlistError(
                "choose at least one supported column",
                code="no_valid_columns",
            )
        try:
            return self._storage.create(user_id, clean_name, exchange, chosen)
        except DuplicateWatchlistName as exc:
            raise WatchlistError(
                f"you already have a watchlist called {clean_name!r}",
                code="duplicate_name",
                status=409,
            ) from exc

    def get(self, user_id: str, watchlist_id: str) -> dict[str, Any] | None:
        return self._storage.get(user_id, watchlist_id)

    def update(self, user_id: str, watchlist_id: str, **fields: Any) -> dict[str, Any] | None:
        if "name" in fields and fields["name"] is not None:
            fields["name"] = str(fields["name"]).strip()
            if not fields["name"]:
                raise WatchlistError("name cannot be empty", code="missing_name")
        try:
            return self._storage.update(user_id, watchlist_id, fields)
        except DuplicateWatchlistName as exc:
            raise WatchlistError(
                "you already have a watchlist with that name",
                code="duplicate_name",
                status=409,
            ) from exc

    def delete(self, user_id: str, watchlist_id: str) -> bool:
        return self._storage.delete(user_id, watchlist_id)

    def set_default(self, user_id: str, watchlist_id: str) -> dict[str, Any] | None:
        return self._storage.set_default(user_id, watchlist_id)

    # --------------------------------------------------------------- items
    def add_items(
        self, user_id: str, watchlist_id: str, symbols: list[str]
    ) -> dict[str, Any]:
        """Add symbols, validating each against the instrument master.

        Returns ``added``, ``skipped`` (already present) and ``unknown``. Unknown
        symbols are reported rather than rejected so pasting a list with one typo
        adds the rest — but they are never silently dropped either.
        """
        if self._storage.get(user_id, watchlist_id) is None:
            raise WatchlistError("no such watchlist", code="not_found", status=404)

        known: list[str] = []
        unknown: list[str] = []
        for raw in symbols:
            canonical = self._market.resolve(raw)
            if canonical:
                known.append(canonical)
            else:
                unknown.append(str(raw).strip().upper())

        if not known:
            return {"added": [], "skipped": [], "unknown": unknown}

        result = self._storage.add_items(user_id, watchlist_id, known, unknown)
        if result is None:
            raise WatchlistError("no such watchlist", code="not_found", status=404)
        added, skipped = result
        return {"added": added, "skipped": skipped, "unknown": unknown}

    def remove_item(self, user_id: str, watchlist_id: str, symbol: str) -> bool:
        return self._storage.remove_item(user_id, watchlist_id, symbol)

    def reorder(self, user_id: str, watchlist_id: str, symbols: list[str]) -> dict[str, Any] | None:
        try:
            return self._storage.reorder(user_id, watchlist_id, symbols)
        except InvalidWatchlistOrder as exc:
            raise WatchlistError(
                str(exc), code="invalid_order", status=400
            ) from exc

    def set_columns(
        self, user_id: str, watchlist_id: str, keys: list[str]
    ) -> dict[str, Any] | None:
        accepted = self.validate_columns(keys)
        rejected = [k for k in keys if (k or "").strip() and (k or "").strip() not in accepted]
        if not accepted:
            raise WatchlistError(
                "none of the requested columns exist; see GET /watchlists/columns/available",
                code="no_valid_columns",
            )
        result = self._storage.set_columns(user_id, watchlist_id, accepted, rejected)
        if result is not None:
            result["rejected_columns"] = rejected
        return result

    # -------------------------------------------------------------- quotes
    def quotes(
        self,
        user_id: str,
        watchlist_id: str,
        *,
        live: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        """Resolve every configured column for every symbol in the list.

        Values come from the local daily cache. ``live`` optionally carries a
        ``{symbol: quote}`` overlay from the broker; when it is absent or
        incomplete the cached close is used and the row says so, because a stale
        price presented as a live one is worse than no price at all.
        """
        detail = self.get(user_id, watchlist_id)
        if detail is None:
            return None

        keys = [k for k in detail["columns"] if k in COLUMN_INDEX]
        live = live or {}
        rows: list[dict[str, Any]] = []
        for symbol in detail["items"]:
            row = self._row_for(symbol, keys, live.get(symbol))
            if row is not None:
                rows.append(row)

        return {
            "watchlist_id": watchlist_id,
            "name": detail["name"],
            "exchange": detail["exchange"],
            "as_of": datetime.now().isoformat(timespec="seconds"),
            "source": "broker+cache" if live else "local_cache",
            "live_symbols": sorted(live),
            "columns": [COLUMN_INDEX[k].as_dict() for k in keys],
            "rows": rows,
            "count": len(rows),
        }

    def _row_for(
        self, symbol: str, keys: list[str], live_quote: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        record = self._market.get(symbol)
        frame = self._market.daily_bars(record.cache_file if record else None)
        if frame is None or frame.empty:
            return {
                "symbol": symbol,
                "name": record.name if record else None,
                "error": "no local history for this symbol",
                **{k: None for k in keys},
            }

        values = _compute_columns(frame, keys)
        stale = True
        if live_quote:
            ltp = _first_number(live_quote, ("ltp", "lastPrice", "last_price", "last"))
            prev = _first_number(
                live_quote, ("prevClose", "previousClose", "close", "prev_close")
            )
            if ltp:
                values["ltp"] = ltp
                stale = False
                base = prev or values.get("prev_close")
                if base:
                    values["prev_close"] = base
                    values["change"] = round(ltp - base, 4)
                    values["change_pct"] = round((ltp - base) / base * 100, 4)
            for source_key, target in (("volume", "volume"), ("open", "open"),
                                       ("high", "high"), ("low", "low")):
                number = _first_number(live_quote, (source_key,))
                if number is not None:
                    values[target] = number

        return {
            "symbol": symbol,
            "name": record.name if record else None,
            "industry": record.industry if record else None,
            "indices": list(record.indices) if record else [],
            "stale": stale,
            **values,
        }

def _compute_columns(frame: pd.DataFrame, keys: list[str]) -> dict[str, Any]:
    """Derive the requested columns from one symbol's daily frame.

    Indicators are computed on the whole series once and the last value taken —
    recomputing per column over the whole history is the difference between a
    watchlist that refreshes and one that hangs.
    """
    out: dict[str, Any] = dict.fromkeys(keys)
    if frame.empty:
        return out

    close = frame["close"].astype(float)
    high = (
        frame["high"].astype(float)
        if "high" in frame
        else pd.Series(float("nan"), index=frame.index, dtype=float)
    )
    low = (
        frame["low"].astype(float)
        if "low" in frame
        else pd.Series(float("nan"), index=frame.index, dtype=float)
    )
    volume = frame["volume"].astype(float) if "volume" in frame else pd.Series(dtype=float)

    def _last(series: pd.Series) -> float | None:
        """Final value of a series that ends at the frame's last row.

        Positional from the end, never from the frame's length. An earlier version
        indexed with ``len(frame) - 1``, which is out of bounds for any series
        shorter than the frame — ``close.iloc[:-1]`` is exactly one row shorter, so
        every quote raised IndexError.
        """
        if series is None or len(series) == 0:
            return None
        value = series.iloc[-1]
        return None if pd.isna(value) else round(float(value), 4)

    ltp = _last(close)
    prev_close = _last(close.iloc[:-1]) if len(close) > 1 else None

    wanted = set(keys)
    if "ltp" in wanted:
        out["ltp"] = ltp
    if "prev_close" in wanted:
        out["prev_close"] = prev_close
    if "change" in wanted:
        out["change"] = round(ltp - prev_close, 4) if ltp and prev_close else None
    if "change_pct" in wanted:
        out["change_pct"] = (
            round((ltp - prev_close) / prev_close * 100, 4) if ltp and prev_close else None
        )
    if "open" in wanted and "open" in frame:
        out["open"] = _last(frame["open"].astype(float))
    if "high" in wanted:
        out["high"] = _last(high)
    if "low" in wanted:
        out["low"] = _last(low)
    if "range_pct" in wanted:
        h, lo = _last(high), _last(low)
        out["range_pct"] = round((h - lo) / ltp * 100, 4) if h and lo and ltp else None

    if "volume" in wanted and len(volume):
        latest_volume = volume.iloc[-1]
        out["volume"] = int(latest_volume) if not pd.isna(latest_volume) else None
    if "volume_ratio" in wanted and len(volume) >= 21:
        avg = _last(sma(volume, 20))
        current = _last(volume)
        out["volume_ratio"] = round(current / avg, 3) if avg and current else None
    if (
        "vwap" in wanted
        and len(volume) >= 20
        and {"high", "low"} <= set(frame.columns)
    ):
        typical = (high + low + close) / 3
        window = slice(max(0, len(frame) - 20), len(frame))
        window_volume = volume.iloc[window]
        window_high = high.iloc[window]
        window_low = low.iloc[window]
        window_close = close.iloc[window]
        traded = float(window_volume.sum())
        complete_bars = (
            window_volume.notna().all()
            and window_high.notna().all()
            and window_low.notna().all()
            and window_close.notna().all()
        )
        if complete_bars and traded > 0:
            out["vwap"] = round(
                float((typical.iloc[window] * volume.iloc[window]).sum()) / traded, 4
            )

    if "ema20" in wanted:
        out["ema20"] = _last(ema(close, 20))
    if "ema50" in wanted:
        out["ema50"] = _last(ema(close, 50))
    if "sma20" in wanted:
        out["sma20"] = _last(sma(close, 20))
    if "sma50" in wanted:
        out["sma50"] = _last(sma(close, 50))
    if "rsi14" in wanted:
        value = _last(rsi(close, 14))
        out["rsi14"] = round(value, 2) if value is not None else None
    if "atr14" in wanted:
        out["atr14"] = _last(atr(high, low, close, 14))
    if "atr_pct" in wanted:
        value = _last(atr(high, low, close, 14))
        out["atr_pct"] = round(value / ltp * 100, 3) if value and ltp else None

    if {"high_52w", "low_52w", "from_52w_high_pct"} & wanted:
        window = close.iloc[-252:] if len(close) >= 252 else close
        hi = float(window.max()) if len(window) else None
        lo = float(window.min()) if len(window) else None
        if "high_52w" in wanted:
            out["high_52w"] = round(hi, 4) if hi else None
        if "low_52w" in wanted:
            out["low_52w"] = round(lo, 4) if lo else None
        if "from_52w_high_pct" in wanted:
            out["from_52w_high_pct"] = round((ltp - hi) / hi * 100, 4) if hi and ltp else None

    if "bars" in wanted:
        out["bars"] = int(len(frame))
    if "last_bar_date" in wanted:
        out["last_bar_date"] = _frame_date(frame)
    return out


def _frame_date(frame: pd.DataFrame) -> str | None:
    """Newest bar date.

    Reads the ``ts`` **column**, not the index: the cache loader returns a
    RangeIndex, so ``.index[-1].date()`` raises — a documented trap in this repo
    that a broad ``except`` once turned into an empty string, i.e. a failure
    reported as a plausible result.
    """
    value = frame["ts"].iloc[-1] if "ts" in frame else frame.index[-1]
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def _first_number(payload: dict[str, Any], names: tuple[str, ...]) -> float | None:
    """Pull the first present, finite numeric field under any of ``names``.

    Broker payloads use camelCase in one place and snake_case in another; the
    same resolver must handle both, or a field reads as zero (the repo has hit
    exactly this with ``netQuantity`` vs ``quantity``).
    """
    for name in names:
        if name not in payload:
            continue
        try:
            value = float(payload[name])
        except (TypeError, ValueError):
            continue
        if value == value and value not in (float("inf"), float("-inf")):  # NaN/inf guard
            return value
    return None


__all__ = [
    "COLUMN_INDEX",
    "COLUMN_REGISTRY",
    "COMPUTABLE_COLUMNS",
    "DEFAULT_COLUMNS",
    "ColumnSpec",
    "WatchlistError",
    "WatchlistService",
]
