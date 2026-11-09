"""The screener service: universes, the scan, ranking, and saving.

What it does
------------

1. Ask market-data ports to resolve a universe and load its daily frames.
2. Validate and evaluate the condition tree for each symbol.
3. Rank the survivors.
4. Return the requested display columns, plus per-symbol evidence.

The data adapter owns the frame cache and the instrument catalog.

Nothing here is incremental. A screen re-evaluates from the cached bars every
time, because an incremental screen that drifts from a full one is a screen you
cannot reproduce — and reproducibility is the whole basis for trusting a result.

Ranking
-------

Ranking is explicit rather than incidental. A screen sorted by whatever key the
dict happened to have is a screen whose order changes between runs, and you
cannot tell whether a symbol moved because the market moved or because the sort
did. ``SORT_FIELDS`` names the permitted keys, with a documented meaning for
each, so the ordering is a stated decision.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import pandas as pd

from forward.core.errors import DuplicateScreenerName, ScreenerError, UnknownScreenerUniverse
from forward.core.screener_ports import ScreenerMarketDataPort, ScreenerStoragePort
from forward.screener.conditions import (
    ConditionError,
    Evidence,
    Node,
    count_nodes,
    evaluate_symbol,
    iter_leaves,
    parse_node,
)

logger = logging.getLogger("forward.screener")

@dataclass(frozen=True)
class ScreenerConfig:
    """Required runtime limits and defaults for one screener service."""

    max_leaves: int
    max_group_count: int
    min_bars: int
    worker_count: int
    default_sort: str
    default_universe: str
    default_exchange: str
    default_result_limit: int
    default_sort_descending: bool


# ---------------------------------------------------------------------------
# columns
# ---------------------------------------------------------------------------
#: The display columns a result row may carry. ``expr`` reads the value from a
#: symbol's frame. Every one is a real computation over cached bars — the list
#: deliberately contains no placeholder zeros.
COLUMN_DEFS: tuple[tuple[str, str, str], ...] = (
    # key, label, indicator (resolved via the screener indicator catalog)
    ("ltp", "LTP", "close"),
    ("change_pct", "Change %", "change_pct"),
    ("gap_pct", "Gap %", "gap_pct"),
    ("volume", "Volume", "volume"),
    ("rel_volume", "Rel Volume", "rel_volume"),
    ("turnover", "Turnover", "turnover"),
    ("rsi14", "RSI", "rsi"),
    ("ema20", "EMA 20", "ema"),
    ("ema50", "EMA 50", "ema"),
    ("sma50", "SMA 50", "sma"),
    ("atr_pct", "ATR %", "atr_pct"),
    ("vwap_20d", "VWAP 20d", "vwap_20d"),
    ("prev_high", "Prev High", "prev_high"),
    ("prev_low", "Prev Low", "prev_low"),
    ("high_52w", "52w High", "high_52w"),
    ("low_52w", "52w Low", "low_52w"),
    ("from_52w_high_pct", "Below 52w High %", "from_52w_high_pct"),
    ("range_pct", "Range %", "range_pct"),
    ("close_in_range", "Close in Range %", "close_in_range"),
    ("volatility_20d_pct", "Stdev 20d %", "volatility_20d_pct"),
    ("ret_1m", "Return 1m %", "ret_1m"),
    ("ret_3m", "Return 3m %", "ret_3m"),
)

#: Columns returned when the caller asks for nothing specific. Chosen to answer
#: "what is this, and why is it interesting" without a second request.
DEFAULT_COLUMNS: tuple[str, ...] = (
    "ltp",
    "change_pct",
    "volume",
    "rel_volume",
    "rsi14",
    "ema20",
    "ema50",
    "atr_pct",
)

#: Per-column period overrides, for the indicators where the display convention
#: differs from the catalog default (EMA 20 vs the catalog's EMA 20 — explicit
#: here so changing a catalog default cannot silently relabel a column).
_COLUMN_PERIODS: dict[str, int | None] = {
    "rsi14": 14,
    "ema20": 20,
    "ema50": 50,
    "sma50": 50,
    "atr_pct": 14,
}

COLUMN_LABELS: dict[str, str] = {key: label for key, label, _ in COLUMN_DEFS}
COLUMN_INDICATORS: dict[str, tuple[str, int | None]] = {
    key: (ind, _COLUMN_PERIODS.get(key)) for key, _, ind in COLUMN_DEFS
}


# ---------------------------------------------------------------------------
# ranking
# ---------------------------------------------------------------------------
#: Permitted sort keys and what each means. A field absent from this map cannot
#: be sorted on, so the ordering is always a stated choice.
SORT_FIELDS: dict[str, str] = {
    "rel_volume": "Relative volume, high to low — the unusual-volume screen",
    "change_pct": "Day change %, high to low — the momentum screen",
    "turnover": "Close x volume, high to low — the liquidity screen",
    "atr_pct": "ATR as % of price, high to low — the volatility screen",
    "rsi14": "RSI 14, high to low — the strength screen",
    "ret_1m": "One-month return %, high to low",
    "from_52w_high_pct": "Distance below the 52-week high, closest first",
    "volume": "Raw volume, high to low",
    "symbol": "Alphabetical — a stable order for reading, not ranking",
}

def _validated_sort(sort: str) -> str:
    """Return a supported sort field or reject the request.

    Args:
        sort: Sort field supplied by the caller.

    Returns:
        The validated sort field.

    Raises:
        ScreenerError: If the field is not in the screener catalog.
    """
    if sort not in SORT_FIELDS:
        raise ScreenerError(
            f"unknown sort field {sort!r}; choose one of: {', '.join(sorted(SORT_FIELDS))}",
            code="invalid_sort",
            status=422,
        )
    return sort


def _sort_value(row: dict[str, Any], field: str) -> Any:
    """The comparable value for a sort field, with a total ordering.

    A missing or unmeasurable value sorts last in every direction rather than
    being treated as zero. Zero is a *plausible* value for most of these columns,
    and a NaN silently coerced to 0 would place an unmeasurable symbol at the
    bottom of a "lowest ATR" screen as if it had been measured.
    """
    if field == "symbol":
        return str(row.get("symbol", ""))
    value = row.get(field)
    if value is None:
        # Fall back to the indicator key for display column names.
        value = row.get(_COLUMN_ALIASES().get(field, field))
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def _COLUMN_ALIASES() -> dict[str, str]:  # noqa: N802 - module-level table
    """Map API sort names to the row keys that actually hold the value."""
    return {
        "rsi14": "rsi14",
        "atr_pct": "atr_pct",
        "rel_volume": "rel_volume",
    }


def rank_rows(
    rows: list[dict[str, Any]], sort: str, *, descending: bool = True
) -> list[dict[str, Any]]:
    """Sort rows by a permitted field, unmeasurable values last.

    The sort is always ascending over a tuple whose first element is
    ``is_missing``; descending is expressed by negating the *value* rather than
    by reversing the whole tuple. Reversing would also reverse the missingness
    flag, which would float every unmeasurable row to the top of a descending
    sort — the opposite of what a trader wants. Symbol stays un-negated in both
    directions so ties resolve A→Z regardless of the metric's direction.
    """
    field = _validated_sort(sort)

    if field == "symbol":
        # The symbol column *is* the sort key, so direction is meaningful here
        # rather than a tiebreak.
        return sorted(rows, key=lambda r: str(r.get("symbol", "")), reverse=descending)

    def key(row: dict[str, Any]):
        value = _sort_value(row, field)
        if value is None:
            # Unmeasurable: flag true, magnitude irrelevant, symbol for stability.
            return (True, 0.0, str(row.get("symbol", "")))
        return (False, -value if descending else value, str(row.get("symbol", "")))

    return sorted(rows, key=key)


# ---------------------------------------------------------------------------
# the service
# ---------------------------------------------------------------------------
class ScreenerService:
    """Runs screens over the local NSE universe."""

    def __init__(
        self,
        storage: ScreenerStoragePort,
        market_data: ScreenerMarketDataPort,
        config: ScreenerConfig,
    ) -> None:
        """Create the screener use case.

        Args:
            storage: Port used to read and write saved screens.
            market_data: Port used to resolve universes and load daily bars.
            config: Required request defaults and compute limits.
        """
        self._storage = storage
        self._market_data = market_data
        self._config = config

    # -- universes ------------------------------------------------------
    def universes(self, exchange: str | None = None) -> list[dict[str, Any]]:
        """Return named universes and their available history counts.

        Args:
            exchange: Exchange segment to report.

        Returns:
            Universe names, labels, member counts, and loadable counts.
        """
        exchange = self._config.default_exchange if exchange is None else exchange
        return self._market_data.universes(exchange)

    def symbols_for(self, universe: str, exchange: str | None = None) -> list[str]:
        """Resolve a universe name to symbols with daily history.

        Args:
            universe: Named universe or ``all``.
            exchange: Exchange segment to scan.

        Returns:
            Symbols that have cached daily bars.

        Raises:
            ScreenerError: If the requested universe is unknown.
        """
        try:
            return self._market_data.symbols_for(
                universe,
                self._config.default_exchange if exchange is None else exchange,
            )
        except UnknownScreenerUniverse as exc:
            raise ScreenerError(str(exc), code="unknown_universe") from exc

    # -- scanning -------------------------------------------------------
    def run(
        self,
        *,
        conditions: Any,
        universe: str | None = None,
        exchange: str | None = None,
        symbols: list[str] | None = None,
        columns: list[str] | None = None,
        sort: str | None = None,
        limit: int | None = None,
        descending: bool | None = None,
    ) -> dict[str, Any]:
        """Evaluate a screen. Returns rows, evidence, and what was scanned."""
        t0 = time.monotonic()
        universe = self._config.default_universe if universe is None else universe
        exchange = self._config.default_exchange if exchange is None else exchange
        sort = _validated_sort(self._config.default_sort if sort is None else sort)
        limit = self._config.default_result_limit if limit is None else limit
        descending = (
            self._config.default_sort_descending
            if descending is None
            else descending
        )

        root = self._prepare_tree(conditions)
        groups, leaves = count_nodes(root)

        exchange = str(exchange).upper()
        wanted = [s.strip().upper() for s in (symbols or []) if str(s).strip()]
        # An explicitly empty list means "no symbols", not "every symbol".
        # Treating it as a filter that happens to be blank would turn a caller's
        # bug into a full-universe scan — slow at best, and at worst a screen
        # over a universe nobody asked for.
        empty_selection = symbols is not None and not wanted

        if empty_selection:
            universe_symbols: list[str] = []
        elif wanted:
            universe_symbols = wanted
        else:
            universe_symbols = self.symbols_for(universe, exchange)

        if not universe_symbols and not empty_selection:
            raise ScreenerError(
                f"universe {universe!r} resolved to no symbols with cached history",
                code="empty_universe",
                status=422,
            )

        frames = self._market_data.frames(exchange, universe_symbols)
        requested = self._resolve_columns(columns, root)

        rows: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        unmeasurable_counts: dict[str, int] = {}

        def _eval_symbol(symbol: str):
            df = frames.get(symbol)
            if df is None:
                return None
            try:
                matched, evidence = evaluate_symbol(
                    df, root, min_bars=self._config.min_bars
                )
            except Exception as exc:
                return ("err", symbol, str(exc)[:160])
            if not matched:
                return ("skip", evidence)
            row = self._build_row(symbol, df, requested)
            row["why"] = [e.as_dict() for e in evidence if e.passed]
            row["evidence"] = [e.as_dict() for e in evidence]
            row["setup"] = _setup_label(evidence)
            return ("match", row, evidence)

        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=self._config.worker_count) as pool:
            for res in pool.map(_eval_symbol, universe_symbols):
                if res is None:
                    continue
                tag = res[0]
                if tag == "err":
                    errors.append({"symbol": res[1], "error": res[2]})
                elif tag == "skip":
                    for e in res[1]:
                        if e.unmeasurable:
                            unmeasurable_counts[e.label] = unmeasurable_counts.get(e.label, 0) + 1
                elif tag == "match":
                    rows.append(res[1])
                    for e in res[2]:
                        if e.unmeasurable:
                            unmeasurable_counts[e.label] = unmeasurable_counts.get(e.label, 0) + 1

        rows = rank_rows(rows, sort, descending=descending)
        matched = len(rows)
        limited = rows[: max(int(limit), 0)] if limit else rows
        elapsed = round(time.monotonic() - t0, 3)

        return {
            "as_of": _as_of(frames),
            "exchange": exchange,
            "universe": universe,
            "universe_size": len(universe_symbols),
            "scanned": len(frames),
            "matched": matched,
            "returned": len(limited),
            "sort": sort,
            "descending": descending,
            "columns": requested,
            "conditions": {
                "groups": groups,
                "leaves": leaves,
                "summary": root.describe(),
            },
            "elapsed_s": elapsed,
            "rows": limited,
            "errors": errors[:20],
            # Surfaced rather than hidden: if a condition was unmeasurable for
            # most of the universe, the screen is telling you about your data,
            # not about the market.
            "warnings": (
                ["no symbols were given, so nothing was scanned"] if empty_selection else []
            )
            + [
                f"{label} could not be computed for {count} scanned symbols"
                for label, count in sorted(unmeasurable_counts.items(), key=lambda kv: -kv[1])[:5]
            ],
        }

    # -- validation -----------------------------------------------------
    def validate(self, conditions: Any) -> dict[str, Any]:
        """Structural validation without scanning. Never raises for data issues.

        Catches :class:`ScreenerError` as well as :class:`ConditionError`,
        because ``_prepare_tree`` deliberately translates parse failures into
        ``ScreenerError`` for the *run* path. Missing that meant a malformed tree
        raised out of ``validate`` — so the one endpoint whose contract is "report,
        do not fail" was returning a 500 for exactly the input it exists to
        describe.
        """
        warnings: list[str] = []
        try:
            root = self._prepare_tree(conditions)
        except ScreenerError as exc:
            return {
                "valid": False,
                "error": str(exc),
                "code": exc.code,
                "path": None,
                "warnings": [],
            }
        except ConditionError as exc:
            return {
                "valid": False,
                "error": str(exc),
                "code": exc.code,
                "path": exc.path,
                "warnings": [],
            }

        groups, leaves = count_nodes(root)
        for leaf in iter_leaves(root):
            spec = leaf.spec
            if not spec.available:
                warnings.append(
                    f"{spec.label} is not available in this build — requires {spec.requires}"
                )
            if leaf.period and not spec.takes_period:
                warnings.append(f"{spec.label} ignores 'period'; the period was not applied")
        return {
            "valid": True,
            "groups": groups,
            "leaves": leaves,
            "summary": root.describe(),
            "warnings": warnings,
        }

    # -- internals ------------------------------------------------------
    def _prepare_tree(self, conditions: Any) -> Node:
        if conditions is None:
            raise ScreenerError("provide at least one condition", code="no_conditions")
        try:
            root = parse_node(
                conditions if isinstance(conditions, dict) else {"conditions": conditions}
            )
        except ConditionError as exc:
            raise ScreenerError(str(exc), code=exc.code) from exc

        groups, leaves = count_nodes(root)
        if leaves == 0:
            raise ScreenerError(
                "the condition tree has no conditions to evaluate",
                code="no_conditions",
            )
        if leaves > self._config.max_leaves:
            raise ScreenerError(
                f"too many conditions ({leaves}); the limit is {self._config.max_leaves}",
                code="too_many_conditions",
            )
        if groups > self._config.max_group_count:
            raise ScreenerError(
                f"condition tree is too nested ({groups} groups)", code="too_nested"
            )
        for leaf in iter_leaves(root):
            spec = leaf.spec
            if not spec.available:
                raise ScreenerError(
                    f"{spec.label} is not available in this build — requires {spec.requires}",
                    code="indicator_unavailable",
                )
        return root

    @staticmethod
    def _resolve_columns(columns: list[str] | None, root: Node) -> list[str]:
        """Display columns: what was asked for, plus what the screen tested.

        The union matters. A screen on "RSI < 35" whose result table has no RSI
        column makes the reader open every row to check the claim, so any
        indicator named in the tree is added to the output whether or not it was
        requested.
        """
        known = set(COLUMN_LABELS)
        requested: list[str] = []
        for key in columns or ():
            k = str(key).strip().lower()
            if k in known and k not in requested:
                requested.append(k)
        if not requested:
            requested = list(DEFAULT_COLUMNS)

        # Add a column for any leaf indicator we have a display column for.
        for leaf in iter_leaves(root):
            key = leaf.indicator
            if key in ("sma", "ema") and leaf.period:
                key = f"{key}{leaf.period}"
            if key in known and key not in requested:
                requested.append(key)
        return requested

    @staticmethod
    def _build_row(symbol: str, df: pd.DataFrame, columns: list[str]) -> dict[str, Any]:
        from forward.screener.indicators import indicator_series, last_value

        row: dict[str, Any] = {"symbol": symbol}
        for key in columns:
            indicator, period = COLUMN_INDICATORS.get(key, (key, None))
            value = last_value(indicator_series(df, indicator, period))
            row[key] = None if value != value else round(value, 4)
        return row

    # -- saved scans ----------------------------------------------------
    def save(
        self,
        user_id: str,
        *,
        name: str,
        definition: dict[str, Any],
        description: str | None = None,
    ) -> dict[str, Any]:
        if not str(name or "").strip():
            raise ScreenerError("a saved scan needs a name", code="missing_name")

        report = self.validate(definition.get("conditions"))
        if not report["valid"]:
            raise ScreenerError(
                f"the scan definition is invalid: {report['error']}",
                code=report.get("code") or "invalid_definition",
            )

        try:
            row = self._storage.create(user_id, name, definition, description)
        except DuplicateScreenerName as exc:
            raise ScreenerError(
                f"a scan named {name!r} already exists", code="name_taken", status=409
            ) from exc
        return _scan_out(row)

    def list_saved(self, user_id: str) -> list[dict[str, Any]]:
        return [_scan_out(row) for row in self._storage.list_for_user(user_id)]

    def list_all(self) -> list[dict[str, Any]]:
        """Return saved screens with owners for the watch scheduler.

        Returns:
            Saved screens for every user.
        """
        return [{**_scan_out(row), "user_id": row["user_id"]} for row in self._storage.list_all()]

    def get_saved(self, user_id: str, scan_id: str) -> dict[str, Any]:
        row = self._storage.get(scan_id, user_id)
        if row is None:
            # 404 rather than 403: a scan that is not yours must not be
            # distinguishable from one that does not exist.
            raise ScreenerError("scan not found", code="not_found", status=404)
        return _scan_out(row)

    def delete_saved(self, user_id: str, scan_id: str) -> bool:
        deleted = self._storage.delete(scan_id, user_id)
        if not deleted:
            raise ScreenerError("scan not found", code="not_found", status=404)
        return True

    def update_saved(
        self,
        user_id: str,
        scan_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        definition: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Edit a saved scan in place. Absent fields are left alone.

        Lives here rather than in the route because the persistence layer is the
        service's business: a route that opens its own session and calls a
        repository directly puts the ownership check somewhere the next caller
        has to remember to repeat it. ``get_saved`` is called first so a foreign
        or missing id fails with the same 404 the other endpoints give, before
        anything is written.

        A new definition is validated before it is stored, for the same reason
        ``save`` validates: a screen that cannot run is worse than no screen.
        """
        existing = self.get_saved(user_id, scan_id)  # 404 early, before touching anything

        if definition is not None:
            # Editing the screen must not silently switch off its watch.
            old_watch = existing["definition"].get("watch")
            if old_watch and "watch" not in definition:
                definition = {**definition, "watch": old_watch}
            report = self.validate(definition.get("conditions"))
            if not report["valid"]:
                raise ScreenerError(
                    f"the scan definition is invalid: {report['error']}",
                    code=report.get("code") or "invalid_definition",
                )

        try:
            changed = self._storage.update(
                scan_id,
                user_id,
                name=name,
                description=description,
                definition=definition,
            )
        except DuplicateScreenerName as exc:
            raise ScreenerError(
                f"a scan named {name!r} already exists",
                code="name_taken",
                status=409,
            ) from exc
        if not changed:
            raise ScreenerError("scan not found", code="not_found", status=404)
        return self.get_saved(user_id, scan_id)

    def run_saved(self, user_id: str, scan_id: str, **overrides: Any) -> dict[str, Any]:
        """Run a stored scan by id, so a saved screen is one call to re-run."""
        saved = self.get_saved(user_id, scan_id)
        definition = saved.get("definition") or {}
        params = {
            "conditions": definition.get("conditions"),
            "universe": definition.get("universe", self._config.default_universe),
            "exchange": definition.get("exchange", self._config.default_exchange),
            "columns": definition.get("columns"),
            "sort": definition.get("sort", self._config.default_sort),
            "limit": definition.get("limit", self._config.default_result_limit),
        }
        params.update({k: v for k, v in overrides.items() if v is not None})
        result = self.run(**params)
        result["saved_scan"] = {"scan_id": saved["scan_id"], "name": saved["name"]}
        return result


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _as_of(frames: dict[str, pd.DataFrame]) -> str | None:
    """The newest bar date across the scanned frames.

    Reported as the data's own date rather than today's, because a screen run on
    a Sunday is a screen of Friday's market and saying otherwise would be a lie
    about what the numbers are.
    """
    newest: Any = None
    for df in frames.values():
        if df is None or df.empty or "ts" not in df.columns:
            continue
        try:
            ts = pd.Timestamp(df["ts"].iloc[-1])
        except Exception:  # noqa: BLE001
            continue
        if newest is None or ts > newest:
            newest = ts
    return None if newest is None else newest.date().isoformat()


def _setup_label(evidence: list[Evidence]) -> str:
    """A short name for the pattern a match represents.

    Derived from which leaves passed, so it is a description of the screen's own
    conditions rather than an editorial comment — "Breakout" here means "the
    breakout conditions you wrote are true", not a recommendation.
    """
    passed = {e.indicator for e in evidence if e.passed}
    if "gap_pct" in passed and "rel_volume" in passed:
        return "Gap + volume"
    if "rel_volume" in passed and ("prev_high" in passed or "high_52w" in passed):
        return "Volume breakout"
    if "rsi" in passed and "from_52w_high_pct" in passed:
        return "Momentum pullback"
    if {"ema", "sma"} & passed:
        return "Trend"
    if "atr_pct" in passed or "volatility_20d_pct" in passed:
        return "Volatility"
    if "change_pct" in passed:
        return "Move"
    return "Match"


def _scan_out(row: dict[str, Any]) -> dict[str, Any]:
    definition = row.get("definition")
    if isinstance(definition, str):
        import json

        definition = json.loads(definition)
    if not isinstance(definition, dict):
        raise ValueError("saved screener definition must be an object")
    return {
        "scan_id": row.get("scan_id"),
        "name": row.get("name"),
        "description": row.get("description"),
        "definition": definition,
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return value.isoformat()
    except AttributeError:
        return str(value)
