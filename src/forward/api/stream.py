"""Broadcast normalized live market quotes to browser WebSocket clients."""

from __future__ import annotations

import asyncio
import threading
import time
from collections import defaultdict, deque
from collections.abc import Iterable, Mapping
from contextlib import suppress
from dataclasses import replace
from datetime import datetime
from typing import Any

import numpy as np
from fastapi import WebSocket
from loguru import logger

from forward.api.stream_protocol import (
    LiveTickPayload,
    encode_status_frame,
    encode_tick_frame,
)
from forward.core.live_feed_ports import LiveMarketFeedPort
from forward.core.market_models import LiveMarketQuote
from forward.core.observed_bars_ports import ObservedBarsPort
from forward.core.session import IST
from forward.services.tick_candles import TickCandleService


def _normalise_universe(
    symbols: Iterable[str] | Mapping[str, Iterable[str]], exchange: str
) -> dict[str, set[str]]:
    """Coerce a symbol universe to ``{exchange: {SYMBOL, ...}}``.

    Accepts either a flat iterable — every symbol on one ``exchange`` — or a
    mapping of exchange to symbols, which is what a caller with deployments on
    more than one exchange has to say. Both are normalised here rather than at
    the call sites so a subscription can never be sent with a symbol whose
    exchange was guessed.
    """
    if isinstance(symbols, Mapping):
        out: dict[str, set[str]] = {}
        for key, values in symbols.items():
            cleaned = {
                str(s).strip().upper() for s in (values or ()) if str(s).strip()
            }
            if cleaned:
                out[str(key).strip().upper()] = cleaned
        return out

    cleaned = {str(s).strip().upper() for s in (symbols or ()) if str(s).strip()}
    return {exchange.strip().upper(): cleaned} if cleaned else {}


class TickBroadcaster:
    """Manage browser subscriptions and publish live market quotes."""

    def __init__(
        self,
        live_feed: LiveMarketFeedPort,
        tick_candles: TickCandleService,
        observed_bars: ObservedBarsPort,
    ) -> None:
        """Create a broadcaster with its live-feed and candle services.

        Args:
            live_feed: Resolves contracts and manages the live feed connection.
            tick_candles: Aggregates buffered quotes into chart candles.
            observed_bars: Collects quote observations into completed session bars.
        """
        self._live_feed = live_feed
        self._tick_candles = tick_candles
        self._active_connections: set[WebSocket] = set()
        self._client_subscriptions: dict[WebSocket, set[str]] = {}
        self._subscribed_symbols: dict[str, int] = {}  # symbol -> ref count
        self._symbol_to_topic: dict[str, str] = {}
        self._topic_to_symbol: dict[str, str] = {}
        self._topic_aliases: dict[str, frozenset[str]] = {}
        self._symbol_to_exchange: dict[str, str] = {}
        self._latest_ticks: dict[str, LiveTickPayload] = {}
        self._tick_recv_time: dict[str, float] = {}  # symbol -> time.time() of last tick
        self._prev_close: dict[str, float] = {}
        #: The universe a *server-side* consumer wants on the feed — today that
        #: is the paper runner, whose deployments must be priced whether or not
        #: anybody has a dashboard open. A set rather than a ref count because
        #: the consumer states the universe it wants, in full, every pass; the
        #: previous statement is the only state needed to compute the delta.
        self._runner_symbols: set[str] = set()
        #: The subset of that universe actually confirmed onto the bridge. Kept
        #: apart from the wish above so a statement made while the bridge was
        #: down is *retried* on the next one, instead of being remembered as
        #: done — a deployment that is silently not on the feed is the failure
        #: this whole path exists to remove.
        self._runner_acked: set[str] = set()
        #: Symbols that resolved to no contract. Remembered so an unresolvable
        #: symbol is attempted once per appearance rather than re-resolved on
        #: every pass, and forgotten as soon as it leaves the universe so a
        #: corrected symbol list gets a fresh attempt.
        self._runner_unresolved: set[str] = set()
        # In-memory time-series ring buffer: keeps last 5,000 raw ticks per symbol
        # O(1) appends, zero disk I/O, ultra-low sub-millisecond memory footprint
        self._ring_buffer: dict[str, deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=5000)
        )

        #: Latest index quote by name ("NIFTY 50", "INDIA VIX"), from the index feed.
        self._latest_index: dict[str, dict[str, Any]] = {}
        self._index_topics: dict[str, str] = {}  # "nseeq/999920000" -> "NIFTY 50"
        self._observed_bars = observed_bars
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def _ensure_live_feed(self) -> bool:
        """Connect the shared feed and restore the active topic subscriptions."""
        with self._lock:
            symbols = [
                symbol
                for symbol, count in self._subscribed_symbols.items()
                if count > 0
            ]
            symbols.extend(self._runner_symbols)
            topics = set(self._symbol_to_topic.values())
            index_topics = list(self._index_topics)
        for symbol in symbols:
            topic = self._symbol_to_topic.get(symbol) or self._resolve_symbol(symbol)
            if topic:
                topics.add(topic)
        return self._live_feed.ensure_connected(
            on_feed=self._on_market_feed,
            on_index=self._on_index_feed,
            on_error=lambda code, message: logger.warning(
                "Bridge tick error {}: {}", code, message
            ),
            feed_topics=sorted(topics),
            index_topics=index_topics,
        )

    #: The indices the Markets page shows live, as (name, topic). The topic is
    #: ``<exchange>/<instrument id>`` from the broker's index contract file; India VIX
    #: is filed under NSEFO, the cash indices under NSEEQ.
    LIVE_INDICES: tuple[tuple[str, str], ...] = (
        ("NIFTY 50", "nseeq/999920000"),
        ("INDIA VIX", "nsefo/999920019"),
    )

    def ensure_indices(self, indices: tuple[tuple[str, str], ...] | None = None) -> bool:
        """Subscribe the live indices on the bridge. Safe to call repeatedly.

        Returns False when there is no broker session. The bridge replays the last
        value on subscribe, so a quote is available at once even outside market hours.
        """
        if not self._ensure_live_feed():
            return False
        with self._lock:
            fresh = [
                (name, topic)
                for name, topic in (indices or self.LIVE_INDICES)
                if topic not in self._index_topics
            ]
            for name, topic in fresh:
                self._index_topics[topic] = name
        if fresh:
            self._live_feed.subscribe_index([topic for _, topic in fresh])
        return True

    def live_feed_connection(self) -> LiveMarketFeedPort | None:
        """Return the shared live-feed port when its connection is ready."""
        return self._live_feed if self._live_feed.is_connected else None

    def _on_index_feed(self, suffix: str, feed: LiveMarketQuote) -> None:
        """Store one normalized index quote."""
        name = self._index_topics.get(suffix)
        if name is None:
            return
        ltp = float(feed.last)
        prev = float(feed.close or 0)
        with self._lock:
            self._latest_index[name] = {
                "name": name,
                "ltp": ltp,
                "prev_close": prev or None,
                "chg": round(ltp - prev, 4) if prev else None,
                "chg_pct": round((ltp / prev - 1) * 100, 2) if prev else None,
                "recv": time.time(),
            }
            self._capture_opening("indices", name, self._latest_index[name])
            recv = self._latest_index[name]["recv"]
            self._observed_bars.add(name, ltp, 0, recv, recv, index=True)

    def _capture_opening(self, group: str, name: str, quote: dict) -> None:
        """Called under the tick lock. Keep the first valid 09:15 observation."""
        import math
        recv = datetime.fromtimestamp(quote["recv"], IST)
        if recv.strftime("%H:%M") != "09:15":
            return
        fields = ("ltp", "prev_close") if group == "indices" else ("open", "prev_close")
        if any(
            not math.isfinite(float(quote.get(key) or 0))
            or float(quote.get(key) or 0) <= 0
            for key in fields
        ):
            return
        if group == "stocks":
            try:
                ts = datetime.fromisoformat(quote["ts"])
            except (ValueError, TypeError):
                return
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=recv.tzinfo)
            ts = ts.astimezone(recv.tzinfo)
            if (
                ts.date() != recv.date()
                or ts.strftime("%H:%M") != "09:15"
                or abs((recv - ts).total_seconds()) > 15
            ):
                return
        day = recv.date().isoformat()
        if getattr(self, "_opening_day", None) != day:
            self._opening_day = day
            self._opening = {"indices": {}, "stocks": {}}
        self._opening[group].setdefault(name, dict(quote))

    def opening_snapshot(self, day: str) -> dict:
        with self._lock:
            if getattr(self, "_opening_day", None) != day:
                return {"indices": {}, "stocks": {}}
            return {group: {name: dict(q) for name, q in values.items()}
                    for group, values in self._opening.items()}

    def latest_indices(self) -> dict[str, dict[str, Any]]:
        """Latest quote per index, with the seconds since it was received."""
        now = time.time()
        with self._lock:
            return {
                name: {**quote, "age_s": round(now - quote["recv"], 1)}
                for name, quote in self._latest_index.items()
            }

    def observed_bars(self, now) -> dict:
        with self._lock:
            return self._observed_bars.snapshot(now)

    def _resolve_symbol(self, symbol: str, exchange: str = "NSEEQ") -> str | None:
        """Resolve a trading symbol (e.g. RELIANCE-EQ) to topic like 'nseeq/2885'."""
        sym_upper = symbol.strip().upper()
        if sym_upper in self._symbol_to_topic:
            topic = self._symbol_to_topic[sym_upper]
            with self._lock:
                self._topic_aliases[topic] = (
                    self._topic_aliases.get(topic, frozenset()) | {sym_upper}
                )
            return topic

        try:
            conid = self._live_feed.resolve_contract_id(sym_upper, exchange.upper())
            prefix = "nseeq" if exchange.upper() == "NSEEQ" else exchange.lower()
            topic = f"{prefix}/{conid}"
            with self._lock:
                self._symbol_to_topic[sym_upper] = topic
                self._topic_to_symbol[topic] = sym_upper
                self._topic_aliases[topic] = (
                    self._topic_aliases.get(topic, frozenset()) | {sym_upper}
                )
                self._symbol_to_exchange[sym_upper] = exchange.upper()
            return topic
        except Exception as e:
            logger.debug("Failed to resolve conid for symbol {}: {}", symbol, e)
            return None

    def _topic_in_use(self, topic: str) -> bool:
        """Caller holds quote lock; aliases share one physical subscription."""
        aliases = self._topic_aliases.get(topic) or {
            s for s, t in self._symbol_to_topic.items() if t == topic}
        return any(
            symbol in self._runner_symbols
            or self._subscribed_symbols.get(symbol, 0) > 0
            for symbol in aliases
        )

    def _on_market_feed(self, topic: str, feed: LiveMarketQuote) -> None:
        """Store a normalized market quote and notify interested clients."""
        symbol = self._topic_to_symbol.get(topic)
        if not symbol:
            return

        ltp = float(feed.last)
        prev = self._prev_close.get(symbol) or float(feed.close or ltp)
        if feed.close:
            self._prev_close[symbol] = float(feed.close)

        chg = round(ltp - prev, 2)
        chg_pct = round((chg / prev * 100) if prev > 0 else 0.0, 2)

        ts_str = feed.traded_at.isoformat()
        depth_list = [
            {
                "price": level.price,
                "quantity": level.quantity,
                "orders": level.order_count,
                "type": level.side,
            }
            for level in feed.depth
        ]

        epoch_now = time.time()
        payload = LiveTickPayload(
            symbol=symbol,
            exchange=self._symbol_to_exchange.get(symbol, "NSEEQ"),
            ltp=ltp,
            last_qty=feed.quantity,
            volume=feed.volume,
            open=feed.open,
            high=feed.high,
            low=feed.low,
            close=feed.close,
            chg=chg,
            chg_pct=chg_pct,
            best_bid=feed.bid,
            best_ask=feed.ask,
            ts=ts_str,
            epoch=epoch_now,
            depth=depth_list if depth_list else None,
        )

        with self._lock:
            aliases = self._topic_aliases.get(topic, frozenset()) | {symbol}
            observation = {
                "epoch": time.time(),
                "ts": ts_str,
                "ltp": ltp,
                "qty": feed.quantity,
                "volume": feed.volume,
                "best_bid": feed.bid,
                "best_ask": feed.ask,
            }
            for alias in aliases:
                self._latest_ticks[alias] = (
                    payload if alias == symbol else replace(payload, symbol=alias)
                )
                self._capture_opening("stocks", alias, {"open": payload.open,
                    "prev_close": payload.close, "recv": epoch_now, "ts": payload.ts})
                self._tick_recv_time[alias] = epoch_now
                try:
                    observed_at = datetime.fromisoformat(payload.ts).timestamp()
                    self._observed_bars.add(alias, ltp, payload.volume, observed_at, epoch_now)
                except (TypeError, ValueError, OverflowError):
                    pass
                self._ring_buffer[alias].append(observation)

        # Broadcast asynchronously to all interested WebSocket connections
        if self._loop and self._loop.is_running():
            for alias in aliases:
                outgoing = payload if alias == symbol else replace(payload, symbol=alias)
                asyncio.run_coroutine_threadsafe(self._broadcast_tick(outgoing), self._loop)

    async def _broadcast_tick(self, tick: LiveTickPayload) -> None:
        msg = encode_tick_frame(tick)
        targets = [
            ws
            for ws, symbols in self._client_subscriptions.items()
            if tick.symbol in symbols
        ]
        if not targets:
            return

        # Sending one socket at a time serialised every fan-out behind the
        # slowest client; a tick is only useful while it's still fresh, so the
        # sends go out concurrently and a single slow/dead socket no longer
        # holds up delivery to everyone else.
        results = await asyncio.gather(
            *(ws.send_text(msg) for ws in targets), return_exceptions=True
        )
        for ws, result in zip(targets, results, strict=True):
            if isinstance(result, Exception):
                self.disconnect(ws)

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._active_connections.add(ws)
        self._client_subscriptions[ws] = set()

        # Send initial status
        bridge_ok = self._ensure_live_feed()
        await ws.send_text(encode_status_frame(bridge_ok, datetime.now().isoformat()))

    def disconnect(self, ws: WebSocket) -> None:
        self._active_connections.discard(ws)
        to_unsub = []
        with self._lock:
            subs = self._client_subscriptions.pop(ws, set())
            for s in subs:
                cnt = self._subscribed_symbols.get(s, 1) - 1
                if cnt <= 0:
                    self._subscribed_symbols.pop(s, None)
                    # Only take the symbol off the feed if nobody else wants it.
                    # A paper deployment's universe is a reason to stay
                    # subscribed that has nothing to do with this socket.
                    if s not in self._runner_symbols:
                        topic = self._symbol_to_topic.get(s)
                        if topic and not self._topic_in_use(topic):
                            to_unsub.append(topic)
                else:
                    self._subscribed_symbols[s] = cnt

            if to_unsub and self._live_feed.is_connected:
                with suppress(Exception):
                    self._live_feed.unsubscribe_feed(to_unsub)

    async def subscribe(self, ws: WebSocket, symbols: list[str], exchange: str = "NSEEQ") -> None:
        new_symbols_for_ws = await asyncio.to_thread(
            self._subscribe_topics, ws, symbols, exchange
        )
        # Immediately return cached latest tick if it is fresh (< 60 s old).
        # A tick from a previous session must never be sent as live data.
        _now = time.time()
        for s in new_symbols_for_ws:
            cached = self._latest_ticks.get(s)
            recv_t = self._tick_recv_time.get(s, 0.0)
            if cached and (_now - recv_t) < 60.0:
                try:
                    await ws.send_text(
                        encode_tick_frame(cached)
                    )
                except Exception:
                    break

    def _subscribe_topics(self, ws: WebSocket, symbols: list[str], exchange: str) -> list[str]:
        """Resolve contracts and contact the broker off the async server loop."""
        to_subscribe_topics = []
        new_symbols_for_ws = []

        # Contract resolution may read a large master; keep it outside the
        # lock used by tick ingestion and current-price readers.
        for s in symbols:
            sym = s.strip().upper()
            if sym and sym not in self._symbol_to_topic:
                self._resolve_symbol(sym, exchange)
        with self._lock:
            if ws not in self._client_subscriptions:
                return []
            for s in symbols:
                sym_clean = s.strip().upper()
                if not sym_clean:
                    continue
                self._client_subscriptions[ws].add(sym_clean)
                new_symbols_for_ws.append(sym_clean)

                curr_cnt = self._subscribed_symbols.get(sym_clean, 0)
                if curr_cnt == 0:
                    topic = self._symbol_to_topic.get(sym_clean)
                    if topic:
                        to_subscribe_topics.append(topic)
                self._subscribed_symbols[sym_clean] = curr_cnt + 1

        # Subscribe on bridge if new topics
        if to_subscribe_topics and self._ensure_live_feed():
            try:
                self._live_feed.subscribe_feed(to_subscribe_topics)
            except Exception as e:
                logger.warning("Bridge subscribe error: {}", e)

        return new_symbols_for_ws

    async def unsubscribe(self, ws: WebSocket, symbols: list[str]) -> None:
        await asyncio.to_thread(self._unsubscribe_topics, ws, symbols)

    def _unsubscribe_topics(self, ws: WebSocket, symbols: list[str]) -> None:
        to_unsub = []
        with self._lock:
            if ws not in self._client_subscriptions:
                return
            for s in symbols:
                sym_clean = s.strip().upper()
                if sym_clean in self._client_subscriptions[ws]:
                    self._client_subscriptions[ws].remove(sym_clean)
                    cnt = self._subscribed_symbols.get(sym_clean, 1) - 1
                    if cnt <= 0:
                        self._subscribed_symbols.pop(sym_clean, None)
                        # The paper runner's universe keeps the symbol on the
                        # feed after the last browser lets it go.
                        if sym_clean not in self._runner_symbols:
                            topic = self._symbol_to_topic.get(sym_clean)
                            if topic and not self._topic_in_use(topic):
                                to_unsub.append(topic)
                    else:
                        self._subscribed_symbols[sym_clean] = cnt

        if to_unsub and self._live_feed.is_connected:
            with suppress(Exception):
                self._live_feed.unsubscribe_feed(to_unsub)

    # ------------------------------------------------------------------
    # Server-side subscriptions — a consumer that is not a browser
    # ------------------------------------------------------------------
    def ensure_symbols(
        self,
        symbols: Iterable[str] | Mapping[str, Iterable[str]],
        exchange: str = "NSEEQ",
    ) -> dict[str, list[str]]:
        """Put a universe on the feed on behalf of a server-side consumer.

        **This is the difference between a paper deployment that trades and one
        that only looks like it does.** A symbol reached the feed only when a
        *browser* asked for it: :meth:`_resolve_symbol` is called from
        :meth:`subscribe` and nowhere else, so with no dashboard open the bridge
        was never connected, no tick ever arrived, and :meth:`latest_price`
        returned ``None`` for every symbol. The paper venue reads "no price" as a
        rejection for a new order and as "resting" for an existing one, so the
        observable result was a deployment that reported itself as running while
        evaluating nothing. A missing live tick leaves an order unfilled; the
        paper venue does not use yesterday's cached close as a substitute.

        **Declarative, not incremental.** The caller states the universe it
        wants, in full; the difference against the previous statement is what is
        sent to the bridge. The runner re-states its universe on every pass, so
        the steady state is a set comparison and no I/O.

        **Separate from the browser ref counts**, so the two kinds of consumer
        cannot unsubscribe each other. Closing a dashboard tab must not stop a
        paper deployment from being priced, and stopping a deployment must not
        blank a chart somebody is watching.

        **A failed statement is retried, not remembered as done.** The desired
        universe and the universe confirmed onto the bridge are tracked
        separately, so a call made while the bridge was down leaves its symbols
        outstanding and the next call sends them again.

        Returns ``{"added", "removed", "unresolved"}`` — the symbols newly put on
        the feed, those taken off, and those that could not be resolved to a
        contract. The last is reported rather than swallowed: a symbol with no
        conid is a deployment that will never be priced from live ticks, and an
        empty list is how a caller tells that apart from success.
        """
        wanted_by_exchange = _normalise_universe(symbols, exchange)
        wanted = {s for group in wanted_by_exchange.values() for s in group}

        with self._lock:
            fresh = wanted - self._runner_acked - self._runner_unresolved
            dropped = self._runner_acked - wanted
            self._runner_symbols = wanted
            # A symbol that has left the universe is forgotten entirely, so
            # putting it back is a new attempt rather than a remembered failure.
            self._runner_unresolved &= wanted
            held = {s: self._subscribed_symbols.get(s, 0) for s in wanted | dropped}

        added: list[str] = []
        unresolved: list[str] = []
        topics: list[str] = []

        # Resolution happens *outside* the lock. ``_resolve_symbol`` may load the
        # instrument master, and holding the lock across that would stall tick
        # ingestion from the bridge thread — the very ticks this method exists to
        # obtain.
        for group_exchange, group in sorted(wanted_by_exchange.items()):
            for symbol in sorted(group):
                if symbol not in fresh:
                    continue
                topic = self._symbol_to_topic.get(symbol) or self._resolve_symbol(
                    symbol, group_exchange
                )
                if topic is None:
                    unresolved.append(symbol)
                    continue
                added.append(symbol)
                # Only reach the exchange if no browser is already carrying it.
                if held.get(symbol, 0) == 0:
                    topics.append(topic)

        removed: list[str] = []
        drop_topics: list[str] = []
        for symbol in sorted(dropped):
            removed.append(symbol)
            if held.get(symbol, 0) == 0:
                topic = self._symbol_to_topic.get(symbol)
                if topic:
                    with self._lock:
                        if not self._topic_in_use(topic):
                            drop_topics.append(topic)

        if topics or drop_topics:
            subscribed = False
            if self._ensure_live_feed():
                try:
                    if topics:
                        self._live_feed.subscribe_feed(topics)
                    if drop_topics:
                        self._live_feed.unsubscribe_feed(drop_topics)
                    subscribed = True
                except Exception as e:  # noqa: BLE001 - the next pass retries
                    logger.warning(
                        "Bridge subscription for the paper universe failed: {}", e
                    )

            if subscribed:
                with self._lock:
                    self._runner_acked |= {s for s in added}
                    self._runner_acked -= set(removed)
            else:
                # Nothing reached the bridge, so nothing is acked: the symbols
                # stay outstanding and the next statement of this universe sends
                # them again rather than assuming the job was done.
                with self._lock:
                    self._runner_acked -= set(removed)

        if unresolved:
            # Attempted once per appearance. Re-resolving a symbol that has no
            # contract on every pass would cost a lookup a second and print the
            # same warning forever; the caller reports it once, which is the
            # signal an operator can act on.
            with self._lock:
                self._runner_unresolved |= set(unresolved)

        return {"added": added, "removed": removed, "unresolved": unresolved}

    # ------------------------------------------------------------------
    # In-Memory Time-Series Ring Buffer Query Engine
    # ------------------------------------------------------------------
    def latest_price(self, symbol: str) -> float | None:
        """The most recent traded price for a symbol, or ``None``.

        The seam the paper engine reads so a fill is priced off the market
        rather than off yesterday's close. Returns the whole number or nothing:
        a NaN or a zero here would become the cost basis of a position, and from
        there every P&L figure downstream.

        Read under the lock because the bridge writes ``_latest_ticks`` from its
        own MQTT thread — a dict mutation concurrent with this read is exactly
        the race the lock exists for.
        """
        sym = symbol.strip().upper()
        with self._lock:
            tick = self._latest_ticks.get(sym)
            recv_t = self._tick_recv_time.get(sym, 0.0)
        if tick is None:
            return None
        # Reject stale ticks — do not let a price from a previous session
        # become the cost basis of a paper fill.
        if (time.time() - recv_t) > 60.0:
            return None
        price = float(tick.ltp)
        return price if price == price and price > 0 else None

    def latest_tick(self, symbol: str) -> LiveTickPayload | None:
        """The most recent full tick payload for a symbol, or None."""
        sym = symbol.strip().upper()
        with self._lock:
            return self._latest_ticks.get(sym)

    def latest_price_and_time(self, symbol: str) -> tuple[float | None, datetime | None]:
        """Return (ltp, tick_datetime) for a symbol."""
        sym = symbol.strip().upper()
        with self._lock:
            tick = self._latest_ticks.get(sym)
        if tick is None:
            return None, None
        price = float(tick.ltp)
        if not (price == price and price > 0):
            return None, None
        dt = None
        if tick.ts:
            try:
                dt = datetime.fromisoformat(tick.ts)
            except (ValueError, TypeError):
                dt = None
        return price, dt

    def get_tick_history(self, symbol: str, limit: int = 100) -> list[dict[str, Any]]:
        """Return the most recent ticks up to `limit` for a symbol."""
        sym_clean = symbol.strip().upper()
        with self._lock:
            buf = self._ring_buffer.get(sym_clean)
            if not buf:
                return []
            ticks = list(buf)
        return ticks[-limit:]

    def get_rolling_vwap(self, symbol: str, window_seconds: int = 900) -> dict[str, Any]:
        """Compute rolling Volume Weighted Average Price (VWAP) over window_seconds.

        Uses NumPy vectorization on the ring buffer snapshot for microsecond latency.
        """
        sym_clean = symbol.strip().upper()
        now = time.time()
        cutoff = now - window_seconds

        with self._lock:
            buf = self._ring_buffer.get(sym_clean)
            if not buf:
                return {
                    "symbol": sym_clean,
                    "vwap": None,
                    "ticks_count": 0,
                    "total_qty": 0,
                    "window_seconds": window_seconds,
                }
            items = [t for t in buf if t["epoch"] >= cutoff]

        # A last-traded price is not a volume-weighted average. Ignore ticks
        # without positive trade size and report no VWAP when no usable trades
        # fall inside the requested time window.
        items = [item for item in items if item["qty"] > 0]
        if not items:
            return {
                "symbol": sym_clean,
                "vwap": None,
                "ticks_count": 0,
                "total_qty": 0,
                "window_seconds": window_seconds,
            }

        prices = np.array([t["ltp"] for t in items], dtype=np.float64)
        quantities = np.array([t["qty"] for t in items], dtype=np.float64)

        total_qty = np.sum(quantities)
        vwap = float(np.sum(prices * quantities) / total_qty)

        return {
            "symbol": sym_clean,
            "vwap": round(vwap, 2),
            "ticks_count": len(items),
            "total_qty": int(total_qty),
            "window_seconds": window_seconds,
        }

    def get_tick_candles(self, symbol: str, interval_seconds: int = 5) -> list[dict[str, Any]]:
        """Resample in-memory ring-buffer ticks into sub-minute candles."""
        sym_clean = symbol.strip().upper()
        with self._lock:
            buf = self._ring_buffer.get(sym_clean)
            if not buf:
                return []
            items = list(buf)

        if not items:
            return []

        return self._tick_candles.resample(
            sym_clean, items, interval_seconds
        )


_BROADCASTER: TickBroadcaster | None = None

def get_broadcaster() -> TickBroadcaster:
    global _BROADCASTER
    if _BROADCASTER is None:
        from forward.composition import (
            build_observed_bars_port,
            get_live_market_feed,
            get_tick_candle_service,
        )

        _BROADCASTER = TickBroadcaster(
            get_live_market_feed(),
            get_tick_candle_service(),
            build_observed_bars_port(),
        )
    return _BROADCASTER
