"""Strategy interface.

Strategies see the world through :class:`StrategyContext` only — they never
touch the broker or portfolio directly. That's what makes the exact same class
run in a backtest and in live trading.

Two-phase design:
  * :meth:`Strategy.prepare` runs once over the full history and is the right
    place for vectorised indicator computation (fast, no look-ahead risk if you
    only use rolling windows).
  * :meth:`Strategy.on_bar` runs bar by bar and may only read rows at or before
    the current index.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import namedtuple
from collections.abc import Callable
from datetime import datetime
from keyword import iskeyword
from math import isfinite

import pandas as pd

from forward.core.enums import OrderType, Side, TimeInForce
from forward.core.instrument_models import Instrument
from forward.core.market_models import Bar
from forward.core.order_models import Order
from forward.core.portfolio_models import Position
from forward.strategy.windows import RollingWindow


class StrategyInputUnavailable(RuntimeError):
    """A strategy cannot evaluate because a required input is missing.

    Args:
        message: The missing input and strategy that needs it.
    """


class StrategyCapabilityUnavailable(RuntimeError):
    """A runner does not provide a capability required by a strategy."""

# --------------------------------------------------------------------------
# Row access
#
# ``ctx.row()`` is called once per symbol per bar, so it has to stay cheap.
# Returning ``frame.iloc[i]`` looks natural but is not: a Series scalar access
# runs through pandas' indexing machinery and costs ~90us, which in a
# 2-symbol 6-month minute backtest added up to ~28s of a 69s run. Instead we
# hand back a tuple built straight from the frame's numpy columns, and keep
# dict-style access working so strategies can use ``row["close"]`` and
# ``row.get("x")`` as before.
# --------------------------------------------------------------------------

#: Names a namedtuple field may not take (tuple methods, namedtuple internals).
_ROW_RESERVED = frozenset(dir(tuple)) | {
    "_fields",
    "_field_defaults",
    "_make",
    "_replace",
    "_asdict",
    "_map",
}

_row_class_cache: dict[tuple[str, ...], type] = {}


class _RowOps(tuple):
    """Adds dict-style access to a namedtuple row.

    Must subclass ``tuple`` so that it precedes ``tuple`` in the MRO of the
    generated row class — otherwise ``tuple.__getitem__`` would shadow this.
    """

    __slots__ = ()
    #: Maps the *original* column name (which may not be a valid identifier)
    #: onto the sanitised namedtuple field name.
    _map: dict[str, str] = {}

    def get(self, key: str, default=None):
        field = type(self)._map.get(key)
        return getattr(self, field) if field is not None else default

    def __getitem__(self, key):
        if isinstance(key, str):
            field = type(self)._map.get(key)
            if field is None:
                raise KeyError(key)
            return getattr(self, field)
        return tuple.__getitem__(self, key)


def _safe_field(name: str, position: int) -> str:
    """A field name ``namedtuple`` will accept.

    It rejects any name beginning with an underscore, so the fallback must not
    use one — a column called ``_mom`` would otherwise raise at row-lookup time
    and break every strategy that used it.
    """
    if (
        name.isidentifier()
        and not iskeyword(name)
        and not name.startswith("_")
        and name not in _ROW_RESERVED
    ):
        return name
    return f"c{position}"


def _row_class(columns: tuple[str, ...]) -> type:
    """Build (and cache) a row class for a given set of frame columns."""
    cached = _row_class_cache.get(columns)
    if cached is not None:
        return cached

    mapping: dict[str, str] = {}
    fields: list[str] = []
    used: set[str] = set()
    for position, column in enumerate(columns):
        field = _safe_field(column, position)
        suffix = 0
        while field in used:
            suffix += 1
            field = f"{_safe_field(column, position)}_{suffix}"
        used.add(field)
        fields.append(field)
        mapping[column] = field

    base = namedtuple("Row", fields)
    cls = type("Row", (base, _RowOps), {"__slots__": (), "_map": mapping})
    _row_class_cache[columns] = cls
    return cls


class StrategyContext:
    """Everything a strategy is allowed to see and do."""

    def __init__(
        self,
        instruments: dict[str, Instrument],
        frames: dict[str, pd.DataFrame],
        submit: Callable[[Order], Order],
        portfolio_getter: Callable,
        window_size: int = 500,
        cancel: Callable[[Order, str], None] | None = None,
        contract_catalog=None,
        watch_contract: Callable[[Instrument], None] | None = None,
        quote_getter: Callable[[str], object | None] | None = None,
        stop_requester: Callable[[], None] | None = None,
    ) -> None:
        self.instruments = instruments
        self.frames = frames
        self._submit = submit
        self._portfolio = portfolio_getter
        self._cancel = cancel
        self.contracts = contract_catalog
        self._watch_contract = watch_contract
        self._quote_getter = quote_getter
        self._stop_requester = stop_requester
        self._window_size = window_size
        self.now: datetime | None = None
        self.index: int = -1
        self.windows: dict[str, RollingWindow] = {
            sym: RollingWindow(window_size) for sym in instruments
        }
        self.bars: dict[str, Bar] = {}
        # Per-symbol cache of (frame length, row class, column arrays). Keyed
        # on length so live trading — where frames grow as ticks arrive —
        # picks up new rows instead of reading a stale snapshot.
        self._prepared: dict[str, tuple[int, type, list]] = {}

    # ------------------------------------------------------------------
    def _columns(self, symbol: str) -> tuple[type, list]:
        frame = self.frames[symbol]
        length = len(frame)
        cached = self._prepared.get(symbol)
        if cached is None or cached[0] != length:
            columns = tuple(str(c) for c in frame.columns)
            arrays = [frame[c].to_numpy() for c in frame.columns]
            cached = (length, _row_class(columns), arrays)
            self._prepared[symbol] = cached
        return cached[1], cached[2]

    @property
    def portfolio(self):
        return self._portfolio()

    @property
    def equity(self) -> float:
        return self.portfolio.equity

    @property
    def cash(self) -> float:
        return self.portfolio.cash

    @property
    def buying_power(self) -> float | None:
        return self.portfolio.buying_power

    # ------------------------------------------------------------------
    def row(self, symbol: str):
        """Current bar plus any precomputed indicator columns.

        Returns a tuple-like row supporting ``row.close``, ``row["close"]``
        and ``row.get("close")`` — but *not* a ``pd.Series``. That is a
        deliberate trade: scalar access is ~1000x faster, which is what a
        per-bar loop needs. Use :meth:`history` if you genuinely want a frame.
        """
        row_class, arrays = self._columns(symbol)
        i = self.index
        return row_class(*(array[i] for array in arrays))

    def history(self, symbol: str, n: int = 200) -> pd.DataFrame:
        """Last ``n`` rows up to and including the current bar."""
        frame = self.frames[symbol]
        start = max(0, self.index - n + 1)
        return frame.iloc[start : self.index + 1]

    def window(self, symbol: str) -> RollingWindow:
        return self.windows[symbol]

    def price(self, symbol: str) -> float | None:
        """Return the current bar close, or ``None`` when no bar exists.

        Args:
            symbol: Instrument symbol.

        Returns:
            Current close or ``None`` when this context has no current bar.
        """
        bar = self.bars.get(symbol)
        return bar.close if bar else None

    @property
    def has_quote_source(self) -> bool:
        """Whether the runner can provide live quotes."""
        return self._quote_getter is not None

    def quote(self, symbol: str):
        """Return a current live tick when the runner exposes one.

        Backtests have bars rather than ticks and return ``None`` here. A
        strategy can use :meth:`price` for its common bar-level logic.
        """
        return self._quote_getter(symbol) if self._quote_getter else None

    def has_quote(self, symbol: str) -> bool:
        """Whether this context has a usable live tick or a current bar."""
        if self._quote_getter:
            quote = self._quote_getter(symbol)
            if quote is None:
                return False
            last = getattr(quote, "last", None)
            return isinstance(last, (int, float)) and isfinite(last) and last > 0
        price = self.price(symbol)
        return price is not None and isfinite(price) and price > 0

    def watch_contract(self, instrument: Instrument) -> None:
        """Make a dynamically selected contract available to the strategy.

        Backtests must include all candidate contracts in their feed. The live
        runner can subscribe to a newly selected contract on demand.
        """
        if self._watch_contract:
            self._watch_contract(instrument)
            self.windows.setdefault(instrument.symbol, RollingWindow(self._window_size))
            return
        if instrument.symbol not in self.instruments:
            raise ValueError(
                f"backtest feed does not include selected contract {instrument.symbol}"
            )

    @property
    def can_request_stop(self) -> bool:
        """Whether this runner can stop its loop after the current strategy call."""
        return self._stop_requester is not None

    @property
    def can_cancel(self) -> bool:
        """Whether this runner can cancel a submitted resting order."""
        return self._cancel is not None

    def request_stop(self) -> None:
        """Ask a live runner to stop cleanly after strategy evaluation."""
        if self._stop_requester:
            self._stop_requester()

    def position(self, symbol: str) -> Position:
        return self.portfolio.position(symbol)

    def has_position(self, symbol: str) -> bool:
        return not self.position(symbol).is_flat

    # ------------------------------------------------------------------
    def order(
        self,
        symbol: str,
        quantity: float,
        order_type: OrderType = OrderType.MARKET,
        *,
        limit_price: float | None = None,
        stop_price: float | None = None,
        tif: TimeInForce = TimeInForce.DAY,
        tag: str | None = None,
        **broker_params,
    ) -> Order | None:
        """Place an order. ``quantity`` is signed: positive buys, negative sells.
        Returns None for a zero-quantity order."""
        if quantity == 0:
            return None
        side = Side.BUY if quantity > 0 else Side.SELL
        order = Order(
            instrument=self.instruments[symbol],
            side=side,
            quantity=abs(quantity),
            order_type=order_type,
            limit_price=limit_price,
            stop_price=stop_price,
            tif=tif,
            tag=tag,
            created_at=self.now,
            broker_params=broker_params,
        )
        return self._submit(order)

    def target(self, symbol: str, target_quantity: float, **kwargs) -> Order | None:
        """Trade the difference between current and desired position."""
        current = self.position(symbol).quantity
        delta = target_quantity - current
        if delta == 0 or abs(delta) < 1e-9:
            return None
        return self.order(symbol, delta, **kwargs)

    def cancel(self, order: Order, reason: str = "cancelled") -> None:
        """Withdraw a resting order.

        Needed by any strategy that keeps a *live* protective stop: re-pricing a
        trailing stop means cancelling the old one first, and without this the
        only alternatives are a fixed stop (no trail) or stacking a new stop
        every bar. A context without cancellation cannot safely run a strategy
        that needs to replace resting orders.

        Raises:
            StrategyCapabilityUnavailable: If the runner has no cancel port.
        """
        if self._cancel is None:
            raise StrategyCapabilityUnavailable(
                "the active runner does not provide order cancellation"
            )
        self._cancel(order, reason)

    def close(self, symbol: str, tag: str = "exit") -> Order | None:
        pos = self.position(symbol)
        if pos.is_flat:
            return None
        return self.order(symbol, -pos.quantity, tag=tag)

    def close_all(self) -> list[Order]:
        orders = []
        for symbol in self.instruments:
            order = self.close(symbol)
            if order:
                orders.append(order)
        return orders


class Strategy(ABC):
    """Base class for all strategies."""

    name: str = "unnamed"

    def __init__(self, **params) -> None:
        self.params = params
        for key, value in params.items():
            setattr(self, key, value)

    # ------------------------------------------------------------------
    def prepare(self, frames: dict[str, pd.DataFrame]) -> None:
        """Optional: add indicator columns to each symbol frame, vectorised."""

    def on_start(self, ctx: StrategyContext) -> None:
        """Called once before the first bar."""

    @abstractmethod
    def on_bar(self, ctx: StrategyContext) -> None:
        """Called on every bar. Submit orders via ``ctx``."""

    def on_session_end(self, ctx: StrategyContext) -> None:
        """Called at each session rollover — default: do nothing."""

    def on_stop(self, ctx: StrategyContext) -> None:
        """Called after the last bar."""

    # ------------------------------------------------------------------
    def describe_signal(self, symbol: str, ctx: StrategyContext) -> str | None:
        """Why this strategy would want to be in ``symbol`` right now.

        Optional, and deliberately not abstract — a strategy that cannot explain
        itself is still a legal strategy, it just produces trades with no
        recorded reason, and the results page says so rather than inventing one.

        When implemented, return a short human sentence built from the *measured*
        values, e.g.::

            "SMA 10 (1,431.82) crossed above SMA 30 (1,420.55)"

        The point is that a backtest trade list you cannot interrogate is a
        number you have to take on faith. This is what makes "why did this trade
        happen" answerable a year later, and it is returned as prose rather than
        a dict because its only consumer is a reader.
        """
        return None

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{type(self).__name__} {self.params}>"
