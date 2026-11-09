"""Backtest runs: submit, execute, analyse, persist.

This is the seam between the API and the existing engine. It does **not** run
bars — :class:a~forward.backtest.engine.BacktestEnginea does that, unchanged. What
lives here is everything around it:

* resolving a config into a concrete feed, strategy and engine config,
* running it on a worker so a submit can return immediately,
* turning a :class:a~forward.backtest.engine.BacktestResulta into the derived series
  the results page needs (drawdown, monthly matrix, enriched trade list),
* persisting all of it so the run can be reopened, and reproduced.

The layering rule for this codebase is aAPI → Service → Domain/Repositorya.
Everything below the route is here so the route can hold no storage logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd

from forward.backtest.config import (
    BacktestRunConfig,
    fingerprint_series,
)
from forward.backtest.sizing import ExitPlan, SizedStrategy, SizingPlan
from forward.backtest.timestamps import timestamp_key
from forward.core.backtest_ports import BacktestMarketDataPort

#: Run statuses, mirroring the check constraint documented in the schema.
QUEUED, RUNNING, COMPLETED, FAILED, CANCELLED = (
    "QUEUED",
    "RUNNING",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
)
TERMINAL = frozenset({COMPLETED, FAILED, CANCELLED})

#: Progress values are coarse on purpose. Fine-grained progress from inside the
#: engine loop would need a callback the engine does not have, and inventing a
#: fake timer-driven percentage would be a progress bar that lies.
PROGRESS_QUEUED = 0.0
PROGRESS_RUNNING = 0.35
PROGRESS_DONE = 1.0


class BacktestError(Exception):
    """A run that could not be submitted or executed."""

    def __init__(self, message: str, *, code: str = "backtest_error", status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


# ---------------------------------------------------------------------------
# Derived analysis — everything the results page reads that the engine does not
# already compute.
# ---------------------------------------------------------------------------
def drawdown_series(equity: pd.Series) -> pd.Series:
    """Fractional drawdown at each point (0.0 at a new high, negative below)."""
    if equity.empty:
        return equity
    running_max = equity.cummax()
    return equity / running_max - 1.0


def monthly_matrix(equity: pd.Series) -> list[dict[str, Any]]:
    """Month-by-month percent returns, one row per calendar month.

    The first month is computed from the run's starting equity rather than from
    the first month-end to the second, so the matrix's first cell reflects the
    actual first month of trading instead of being blank.

    Returns one row per (year, month). The caller pivots into a year × 12 grid;
    returning the grid itself from here would bury an ordering assumption
    (January first, NaN for a month the run did not cover) inside a function
    whose job is to compute returns.
    """
    if equity.empty or len(equity) < 2:
        return []

    frame = pd.DataFrame({"equity": equity})
    frame["year"] = frame.index.year
    frame["month"] = frame.index.month

    # Last equity value observed in each calendar month, in chronological order.
    month_end = frame.groupby(["year", "month"])["equity"].last().sort_index()
    # The run's starting equity anchors the first month's return.
    previous = float(equity.iloc[0])
    rows: list[dict[str, Any]] = []
    for (year, month), value in month_end.items():
        value = float(value)
        pct = ((value / previous) - 1.0) * 100.0 if previous > 0 else 0.0
        rows.append({"year": int(year), "month": int(month), "return_pct": pct})
        previous = value
    return rows


def enrich_trades(
    trades: pd.DataFrame,
    *,
    annotations: list[dict] | None = None,
) -> list[dict[str, Any]]:
    """Turn engine trades into rows for the UI and database.

    The engine records FIFO trades. Annotations add the reason for each trade.

    Signal annotations use the signal bar time. The engine uses the next bar's
    open as the fill time to avoid look-ahead. The function matches trades and
    annotations in order, so a one-bar time difference does not lose the reason.

    Args:
        trades: Completed trades from the backtest engine.
        annotations: Strategy reasons, ordered by symbol and signal time.

    Returns:
        Trade rows with their matching strategy annotations.
    """
    if trades is None or trades.empty:
        return []

    # symbol -> annotations in time order
    pending: dict[str, list[dict]] = {}
    for note in annotations or []:
        pending.setdefault(str(note.get("symbol")), []).append(note)
    for notes in pending.values():
        notes.sort(key=lambda n: timestamp_key(n.get("entry_ts")))

    def take(symbol: str, entry_ts: Any) -> dict:
        """The annotation for this entry, consuming it so it is used once.

        Consuming matters: a symbol that enters, exits, and re-enters twice
        produces two round trips, and the same annotation must not be handed to
        both. Without this the second trade inherits the first trade's reason.
        """
        notes = pending.get(symbol)
        if not notes:
            return {}
        key = timestamp_key(entry_ts)
        best: int | None = None
        for i, note in enumerate(notes):
            if timestamp_key(note.get("entry_ts")) <= key:
                best = i
            else:
                break
        if best is None:
            return {}
        return notes.pop(best)

    rows: list[dict[str, Any]] = []
    for _, row in trades.iterrows():
        entry_ts = row.get("entry_ts")
        symbol = str(row.get("symbol", ""))
        note = take(symbol, entry_ts)

        quantity = _finite(row.get("quantity"))
        direction = "SHORT" if str(row.get("side", "")).upper().startswith("S") else "LONG"
        # A position of unknown sign is treated as long: this codebase's
        # default is aallow_short=Falsea, so long is the likelier truth and the
        # direction is only a label on a P&L that is already computed.
        rows.append(
            {
                "symbol": symbol,
                "direction": direction,
                "quantity": abs(quantity) if quantity is not None else 0.0,
                "entry_ts": _to_dt(entry_ts),
                "entry_price": _finite(row.get("entry_price")) or 0.0,
                "exit_ts": _to_dt(row.get("exit_ts")),
                "exit_price": _finite(row.get("exit_price")),
                "gross_pnl": _finite(row.get("gross_pnl")) or 0.0,
                "commission": _finite(row.get("commission")) or 0.0,
                "net_pnl": _finite(row.get("net_pnl")) or 0.0,
                "return_pct": _finite(row.get("return_pct")),
                "duration_days": _finite(row.get("duration_days")),
                # No annotation means no protective level fired, so the inner
                # strategy's own exit signal closed it. That is the honest
                # default rather than a guess.
                "exit_reason": note.get("exit_reason") or "signal",
                "signal_reason": note.get("signal_reason"),
            }
        )
    return rows


def _to_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.to_pydatetime() if hasattr(value, "to_pydatetime") else value
    try:
        return pd.Timestamp(value).to_pydatetime()
    except (ValueError, TypeError):
        return None


def _finite(value: Any) -> float | None:
    """Coerce to a JSON-safe float, or None. NaN/Inf must never reach JSON."""
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out or out in (float("inf"), float("-inf")):  # NaN / Inf
        return None
    return out


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------
@dataclass
class RunOutcome:
    """What a completed run produced, before it is persisted."""

    metrics: dict[str, Any]
    trades: list[dict[str, Any]]
    equity: list[tuple[datetime, float]]
    drawdown: list[tuple[datetime, float]]
    exposure: list[tuple[datetime, float]]
    monthly: list[dict[str, Any]]
    data_fingerprint: str
    warnings: list[str]
    trades_truncated: bool


class BacktestRunner:
    """Builds and executes one run from one config. No database access."""

    def __init__(
        self,
        config: BacktestRunConfig,
        *,
        version_definition: dict | None = None,
        universe_resolver=None,
        market_data: BacktestMarketDataPort,
        max_persisted_trades: int,
        max_curve_points: int,
    ) -> None:
        """Create a runner for one validated backtest.

        Args:
            config: Rules and date range for the run.
            version_definition: Saved strategy definition, when the run uses one.
            universe_resolver: Service callback for named symbol universes.
            market_data: Port for loading frames and building the engine feed.
            max_persisted_trades: Maximum trade rows persisted for one run.
            max_curve_points: Maximum points kept in each result curve.
        """
        self.config = config
        self.warnings: list[str] = []
        #: The unclipped frames, so the warmup widening can reach back past the
        #: requested start date without a second read of the cache.
        self._raw_frames: dict[str, pd.DataFrame] = {}
        #: Injected by the services layer. This module is a *compute* layer and
        #: may not import the user store, so anything it needs from persistence
        #: arrives as an argument rather than as an import. That boundary is
        #: enforced by aatests/test_architecture.pyaa.
        self._version_definition = version_definition
        self._universe_resolver = universe_resolver
        self._market_data = market_data
        self._max_persisted_trades = max_persisted_trades
        self._max_curve_points = max_curve_points

    # ------------------------------------------------------------------
    def build_strategy(self):
        """Resolve the config's strategy reference into an instance.

        Two ways to name a strategy, and they are not interchangeable:

        * aaengine_keyaa — a built-in in the aaSTRATEGIESaa registry. This is
          what the CLI and the existing UI use.
        * aa(strategy_id, strategy_version)aa — a *saved* definition the user
          created. Its aadefinitionaa JSON holds the constructor params.

        A version is loaded by its exact number, never "latest", because a run
        whose subject can change after the fact is not reproducible.
        """
        from forward.strategy.strategies import STRATEGIES

        key = self.config.engine_key or self.config.strategy
        params = dict(self.config.params)

        if self.config.strategy_id and self.config.strategy_version is not None:
            definition = self._version_definition
            if definition is None:
                raise BacktestError(
                    f"strategy version {self.config.strategy_id}#"
                    f"{self.config.strategy_version} was not loaded",
                    code="version_not_found",
                    status=404,
                )
            key = definition.get("engine_key") or key
            stored = definition.get("params")
            if isinstance(stored, dict):
                # Explicit run params win over the stored definition, so a user
                # can vary one parameter without forking a version.
                params = {**stored, **params}

        if key == "python_rules":
            code = (self._version_definition or {}).get("code") or {}
            params["source"] = code.get("source", "")

        strategy_cls = STRATEGIES.get(key)
        if strategy_cls is None:
            known = ", ".join(sorted(STRATEGIES))
            raise BacktestError(
                f"unknown strategy {key!r}. Known: {known}",
                code="unknown_strategy",
                status=422,
            )

        try:
            inner = strategy_cls(**params) if params else strategy_cls()
        except TypeError as exc:
            raise BacktestError(
                f"{key} does not accept those parameters ({exc}). "
                f"Tunable: {self._tunable(key)}",
                code="bad_strategy_params",
                status=422,
            ) from exc

        sizing = self.config.sizing
        stops = self.config.stops
        return SizedStrategy(
            inner,
            sizing=SizingPlan(
                mode=sizing.mode,
                # apercenta/aquantitya are only required — and only sent by the
                # client — for the mode that uses them (see SizingSpec.validate);
                # the other is aNonea and must not reach an unguarded division.
                fraction=(sizing.percent / 100.0 if sizing.percent is not None else 0.10),
                quantity=(sizing.quantity if sizing.quantity is not None else 100.0),
                max_position_fraction=sizing.max_position_pct / 100.0,
                rounding_rule=sizing.rounding_rule,
                available_capital_fraction=sizing.available_capital_fraction,
            ),
            exits=ExitPlan(
                stop_loss=(
                    stops.stop_loss_pct / 100.0
                    if stops.stop_loss_pct is not None
                    else None
                ),
                take_profit=(
                    stops.take_profit_pct / 100.0
                    if stops.take_profit_pct is not None
                    else None
                ),
                trailing_stop=(
                    stops.trailing_stop_pct / 100.0
                    if stops.trailing_stop_pct is not None
                    else None
                ),
            ),
            allow_short=bool(self.config.allow_short),
        )

    @staticmethod
    def _tunable(key: str) -> str:
        from forward.strategy.strategies import STRATEGIES

        cls = STRATEGIES.get(key)
        if cls is None:
            return "unknown"
        import inspect

        sig = inspect.signature(cls.__init__)
        names = [p for p in sig.parameters if p not in ("self", "params", "kwargs")]
        return ", ".join(names) or "none"

    # ------------------------------------------------------------------
    def resolve_symbols(self) -> list[str]:
        """The concrete symbol list this run will load."""
        if self.config.symbols:
            return [s.strip().upper() for s in self.config.symbols if str(s).strip()]
        if self.config.universe:
            if self._universe_resolver is None:
                raise BacktestError(
                    "this runner has no universe resolver, so a named universe "
                    "cannot be expanded — pass explicit symbols instead",
                    code="no_universe_resolver",
                    status=422,
                )
            return list(self._universe_resolver(self.config.universe, self.config.exchange))
        return []

    def load_market_frames(
        self, symbols: list[str]
    ) -> tuple[dict[str, pd.DataFrame], list[str]]:
        """Load frames for readiness checks and feed construction.

        Args:
            symbols: Trading symbols requested by the run.

        Returns:
            Frames keyed by canonical symbol and data selection warnings.
        """
        frames, warnings = self._market_data.load_frames(
            symbols,
            self.config.exchange,
            self.config.timeframe,
            self.config.end,
        )
        return dict(frames), warnings

    # ------------------------------------------------------------------
    def build_feed(self):
        """A feed over cached dailies, clipped to the requested window."""
        symbols = self.resolve_symbols()
        if not symbols:
            raise BacktestError(
                "no symbols resolved — the universe is empty or nothing is cached",
                code="empty_universe",
                status=422,
            )

        frames, data_warnings = self.load_market_frames(symbols)
        self.warnings.extend(data_warnings)
        if not frames:
            raise BacktestError(
                f"none of the {len(symbols)} requested symbols are in the "
                f"{self.config.exchange} {self.config.timeframe} cache. Run forward history sync "
                f"--exchange {self.config.exchange} first.",
                code="no_data",
                status=422,
            )
        # Keep the full series before any date filter, for warmup widening.
        self._raw_frames = {symbol: index_by_ts(frame) for symbol, frame in frames.items()}

        dropped = sorted(set(symbols) - set(frames))
        if dropped:
            self.warnings.append(
                f"{len(dropped)} of {len(symbols)} symbols had no cached bars and were "
                f"skipped (e.g. {', '.join(dropped[:5])})"
            )

        start, end = self.config.start_date, self.config.end_date
        # The cache stores atsa as a *column* on a RangeIndex, and everything
        # downstream (metrics, monthly grouping, the equity curve index) needs a
        # DatetimeIndex. Normalise once, here.
        frames = {symbol: index_by_ts(frame) for symbol, frame in frames.items()}

        if start or end:
            clipped: dict[str, pd.DataFrame] = {}
            for symbol, frame in frames.items():
                mask = pd.Series(True, index=frame.index)
                if start:
                    mask &= frame.index >= pd.Timestamp(start)
                if end:
                    # Inclusive end date: a user asking for "to 2026-09-11"
                    # means to include that session, not to stop the day before.
                    mask &= frame.index < pd.Timestamp(end) + pd.Timedelta(days=1)
                subset = frame.loc[mask]
                if len(subset):
                    clipped[symbol] = subset
            if not clipped:
                raise BacktestError(
                    f"no bars fall between {start or 'the beginning'} and "
                    f"{end or 'the end'}. The cache covers "
                    f"{_available_span(frames)}; run forward history sync "
                    f"--exchange {self.config.exchange} for more history.",
                    code="empty_date_range",
                    status=422,
                )
            frames = clipped

        # Warmup is different from a date filter: a user asking for "since
        # January" wants those bars *scored*, and the strategy still needs its
        # indicator history before January. So the load window is widened by the
        # warmup length, and the engine is told to skip that many leading bars.
        need = self.config.warmup_bars
        if need is None:
            need = self._warmup_need(self.config.engine_key or self.config.strategy)
        if (self.config.engine_key or (self._version_definition or {}).get("engine_key") or self.config.strategy) == "python_rules":
            from forward.strategy.python_import import code_rules
            rules = code_rules(self._version_definition or {})
            need = max(int(need or 0), rules["entry"]["min_history_bars"], rules["exit"]["min_history_bars"])
        need = int(need or 0)
        if (self.config.engine_key or self.config.strategy) == 'sma_crossover':
            need = max(need, int(self.config.params.get('slow', 50)))

        if need and (start or end):
            widened: dict[str, pd.DataFrame] = {}
            for symbol, frame in frames.items():
                extra = frame.index[0] - pd.Timedelta(days=int(need * 1.6) + 7)
                full = index_by_ts(self._raw_frames.get(symbol, frame))
                mask = full.index >= extra
                if end:
                    mask &= full.index < pd.Timestamp(end) + pd.Timedelta(days=1)
                if self.config.timeframe != "1d" and start:
                    # Intraday warmup counts candles, not calendar days. Loading
                    # hundreds of days for 260 minutes needlessly expands runs.
                    prior = full.loc[full.index < pd.Timestamp(start)].tail(need)
                    scored = full.loc[full.index >= pd.Timestamp(start)]
                    if end:
                        scored = scored.loc[scored.index < pd.Timestamp(end) + pd.Timedelta(days=1)]
                    widened[symbol] = pd.concat([prior, scored])
                else:
                    widened[symbol] = full.loc[mask] if len(full) else frame
            frames = widened
            if start:
                # Count actual pre-start sessions, not an approximate calendar span.
                dates = sorted({ts for frame in frames.values() for ts in frame.index})
                need = sum(ts < pd.Timestamp(start) for ts in dates)

        available = max((len(f) for f in frames.values()), default=0)
        if need and available <= need:
            raise BacktestError(
                f"{need} bars of warmup are needed but the longest series in this "
                f"window has {available}. Widen the date range, or the strategy "
                f"would never get to trade.",
                code="warmup_exceeds_history",
                status=422,
            )
        if need:
            self.warnings.append(
                f"{need} leading bars were used for indicator warmup and not scored"
            )

        return self._make_feed(frames), frames, need

    @staticmethod
    def _warmup_need(key: str) -> int:
        """Bars of history a strategy needs before its rules can fire."""
        from forward.backtest.config import warmup_bars_for

        return warmup_bars_for(key)

    def _make_feed(self, frames: dict[str, pd.DataFrame]):
        """Build the market feed through the configured data adapter.

        Args:
            frames: Clipped and warmup-aware market frames.

        Returns:
            The feed consumed by the backtest engine.
        """
        return self._market_data.build_feed(
            frames, self.config.exchange, self.config.timeframe
        )

    # ------------------------------------------------------------------
    def execute(self) -> RunOutcome:
        """Run the engine and derive everything the results page needs."""
        from forward.backtest.engine import BacktestConfig, BacktestEngine
        from forward.config.costs import (
            commission_model,
            indian_delivery_costs,
            indian_intraday_costs,
            slippage_model,
        )
        from forward.execution.risk import RiskLimits

        feed, frames, warmup = self.build_feed()
        strategy = self.build_strategy()

        costs = self.config.costs
        if costs.model == "india_delivery":
            commission = indian_delivery_costs()
        elif costs.model == "india_intraday":
            commission = indian_intraday_costs()
        elif costs.model == "flat_per_share":
            commission = commission_model()
        else:
            commission = _NoCosts()

        if self.config.costs.model == "flat_per_share":
            self.warnings.append(
                "cost model 'flat_per_share' is the IBKR-style default: it charges "
                "no STT, stamp duty or GST, and understates an Indian delivery round "
                "trip by roughly 0.25% of turnover."
            )

        from forward.backtest.validity import validity_warnings

        self.warnings.extend(
            validity_warnings(
                frames, allow_short=bool(self.config.allow_short), cost_model=str(costs.model)
            )
        )

        cash = self.config.initial_cash
        engine_config = BacktestConfig(
            initial_cash=cash,
            commission=commission,
            slippage=slippage_model(bps=costs.slippage_bps),
            allow_short=self.config.allow_short,
            risk_free_rate=self.config.risk_free_rate,
            square_off_eod=self.config.square_off_eod,
            entry_cutoff=self.config.entry_cutoff,
            square_off_time=self.config.square_off_time,
            participation_rate=float(self.config.participation_rate),
            warmup_bars=warmup,
            # Same discipline as the CLI: a daily loss limit proportionate to
            # the account, so a run cannot quietly lose everything.
            risk=RiskLimits(max_daily_loss=cash * 0.10),
        )

        result = BacktestEngine(feed, strategy, engine_config).run()

        metrics = dict(result.metrics.as_dict())
        metrics["killed"] = result.killed
        metrics["kill_reason"] = result.kill_reason
        metrics["strategy"] = result.strategy_name
        metrics["symbols"] = len(frames)
        metrics["started_at"] = _to_dt(result.equity.index[0]).isoformat() if len(result.equity) else None
        metrics["ended_at"] = _to_dt(result.equity.index[-1]).isoformat() if len(result.equity) else None

        trades = enrich_trades(result.trades, annotations=strategy.annotations)
        truncated = len(trades) > self._max_persisted_trades
        if truncated:
            self.warnings.append(
                f"{len(trades):,} trades were produced; the first "
                f"{self._max_persisted_trades:,} were stored. Aggregate metrics cover all of them."
            )
            trades = trades[: self._max_persisted_trades]
        metrics["trades_truncated"] = truncated

        if not trades:
            self.warnings.append(
                "the run produced no closed trades — the window may be shorter than "
                "the strategy's warmup, or its rules never fired"
            )

        equity = [(ts, float(v)) for ts, v in result.equity.items()]
        dd = drawdown_series(result.equity)
        drawdown = [(ts, float(v)) for ts, v in dd.items()]
        exposure = [(ts, float(v)) for ts, v in result.exposure.items()]

        return RunOutcome(
            metrics=metrics,
            trades=trades,
            equity=_thin(equity, self._max_curve_points),
            drawdown=_thin(drawdown, self._max_curve_points),
            exposure=_thin(exposure, self._max_curve_points),
            monthly=monthly_matrix(result.equity),
            data_fingerprint=fingerprint_series(frames),
            warnings=self.warnings,
            trades_truncated=truncated,
        )


class _NoCosts:
    """A commission model that charges nothing. Only for explicit A/B checks."""

    def compute(self, quantity, price, instrument, side=None) -> float:  # noqa: ANN001, ARG002
        return 0.0


def _thin(points: list[tuple[Any, float]], limit: int) -> list[tuple[Any, float]]:
    """Evenly subsample a curve, always keeping the first and last point.

    Dropping points changes the *shape* of a drawdown, so a thinned curve is
    reported as thinned. Keeping the endpoints means the summary numbers derived
    from a glance still agree with the metrics block.
    """
    if len(points) <= limit:
        return points
    step = len(points) / limit
    picked = [points[int(i * step)] for i in range(limit)]
    if picked[-1] != points[-1]:
        picked[-1] = points[-1]
    return picked


def index_by_ts(frame: pd.DataFrame) -> pd.DataFrame:
    """Return aaframeaa with its aatsaa column as a sorted DatetimeIndex.

    The parquet cache stores aatsaa as a column on a RangeIndex. Every consumer
    downstream — date clipping, the metrics' annualisation, the monthly matrix,
    the equity curve's x-axis — assumes a DatetimeIndex, and each one that
    assumed wrongly produced a different silent wrong answer instead of an error.
    Normalising at the boundary is cheaper than defending in five places.
    """
    if frame is None or len(frame) == 0:
        return frame
    if isinstance(frame.index, pd.DatetimeIndex):
        return frame.sort_index()
    if "ts" not in frame.columns:
        raise BacktestError(
            "cached frame has no atsa column and no datetime index",
            code="bad_cache_frame",
            status=422,
        )
    out = frame.set_index("ts")
    if not isinstance(out.index, pd.DatetimeIndex):
        out.index = pd.DatetimeIndex(out.index)
    return out.sort_index()


def _available_span(frames: dict[str, pd.DataFrame]) -> str:
    """Human description of the date range actually available."""
    firsts = [f.index[0] for f in frames.values() if len(f)]
    lasts = [f.index[-1] for f in frames.values() if len(f)]
    if not firsts:
        return "no data"
    return f"{min(firsts).date()} to {max(lasts).date()}"


__all__ = [
    "BacktestError",
    "BacktestRunner",
    "CANCELLED",
    "COMPLETED",
    "FAILED",
    "QUEUED",
    "RUNNING",
    "RunOutcome",
    "TERMINAL",
    "drawdown_series",
    "enrich_trades",
    "index_by_ts",
    "monthly_matrix",
]
