"""Historical and live market data feeds backed by IIFL Capital."""

from __future__ import annotations

import math
import queue
import threading

import pandas as pd
from loguru import logger

from forward.brokers.iifl.auth import Session
from forward.brokers.iifl.bridge import BridgeClient
from forward.core.instrument_models import Instrument
from forward.core.live_feed_ports import LiveFeedConnectionPort
from forward.core.market_models import Bar, MarketSnapshot, Tick
from forward.data.aggregator import TickToBarAggregator

_TOPIC = {
    "NSEEQ": "nseeq",
    "BSEEQ": "bseeq",
    "NSEFO": "nsefo",
    "BSEFO": "bsefo",
    "NSECOMM": "nsecomm",
    "MCXCOMM": "mcxcomm",
    "NCDEXCOMM": "ncdexcomm",
    "NSECURR": "nsecurr",
    "BSECURR": "bsecurr",
    "INDICES": "nseeq",
}


def exchange_topic(exchange: str) -> str:
    normalized = exchange.upper()
    try:
        return _TOPIC[normalized]
    except KeyError as exc:
        raise ValueError(f"unsupported IIFL bridge exchange: {exchange!r}") from exc


def bridge_topic(instrument: Instrument) -> str:
    if instrument.conid is None:
        raise ValueError(f"instrument {instrument.symbol} has no instrumentId")
    return f"{exchange_topic(instrument.exchange)}/{instrument.conid}"


#: Column order IIFL uses when candles come back as positional arrays.
_CANDLE_FIELDS = ("ts", "open", "high", "low", "close", "volume")


def _candles(payload) -> list:
    """Pull the candle rows out of a historical-data response.

    The API wraps them as ``{"result": [{"candles": [...]}]}``. Iterating the
    response body directly yields the string ``"result"``, which is how this
    previously failed with ``'str' object has no attribute 'get'``.
    """
    if isinstance(payload, dict):
        result = payload.get("result")
        if isinstance(result, dict) and isinstance(result.get("candles"), list):
            return result["candles"]
        if isinstance(result, list):
            if (
                result
                and isinstance(result[0], dict)
                and str(result[0].get("status", "")).startswith("EC")
            ):
                status = result[0]["status"]
                message = result[0].get("message") or status
                raise RuntimeError(str(message))
            if (
                result
                and isinstance(result[0], dict)
                and isinstance(result[0].get("candles"), list)
            ):
                return result[0]["candles"]
            if all(isinstance(row, (dict, list, tuple)) for row in result):
                return result
            raise ValueError("IIFL historical response contains an invalid candle row")
        if result == []:
            return []
        raise ValueError("IIFL historical response has no candle result")
    if isinstance(payload, list):
        return payload
    raise ValueError("IIFL historical response must be an object or candle list")


def _candle_row(row) -> dict:
    """Normalise one candle to a dict.

    Candles arrive as positional arrays ``[ts, open, high, low, close, volume]``
    rather than objects, so dict access alone is not enough.
    """
    if isinstance(row, dict):
        ts = next(
            (row[key] for key in ("initialTimestamp", "timestamp", "ts") if row.get(key) is not None),
            None,
        )
        if ts is None:
            raise ValueError("IIFL candle has no timestamp")
        return {
            "ts": ts,
            "open": row.get("open"),
            "high": row.get("high"),
            "low": row.get("low"),
            "close": row.get("close"),
            "volume": row.get("volume"),
        }
    if isinstance(row, (list, tuple)) and len(row) == len(_CANDLE_FIELDS):
        return dict(zip(_CANDLE_FIELDS, row, strict=False))
    raise ValueError(f"IIFL candle has an invalid shape: {row!r}")


def _validated_candle(row: dict) -> dict:
    """Validate required candle values without replacing missing data."""
    timestamp = pd.to_datetime(row["ts"])
    if pd.isna(timestamp):
        raise ValueError(f"IIFL candle has an invalid timestamp: {row['ts']!r}")

    values: dict[str, float] = {}
    for field in ("open", "high", "low", "close", "volume"):
        raw = row[field]
        if raw is None:
            raise ValueError(f"IIFL candle has no {field} value")
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError(f"IIFL candle has a non-finite {field} value")
        if field == "volume":
            if value < 0:
                raise ValueError("IIFL candle volume cannot be negative")
        elif value <= 0:
            raise ValueError(f"IIFL candle {field} must be positive")
        values[field] = value
    return {"ts": timestamp, **values}


def candles_to_frame(payload) -> pd.DataFrame:
    """Convert IIFL candle rows to a sorted pandas OHLCV frame.

    Args:
        payload: Raw IIFL historical data response.

    Returns:
        Rows ordered by timestamp with duplicate timestamps removed.
    """
    rows = []
    for raw in _candles(payload):
        rows.append(_validated_candle(_candle_row(raw)))
    if not rows:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
    return (
        pd.DataFrame(rows)
        .sort_values("ts")
        .drop_duplicates("ts", keep="last")
        .reset_index(drop=True)
    )


class IiflLiveFeed:
    """Realtime ticks over the MQTT bridge, aggregated into bars.

    Usage::

        from forward.config.settings import get_settings

        feed = IiflLiveFeed(
            session,
            instruments,
            tls_verify=get_settings().bridge_tls_verify,
            freq="1min",
        )
        feed.start()
        snapshot = feed.get(timeout=1.0)   # MarketSnapshot
        feed.stop()
    """

    def __init__(
        self,
        session: Session,
        instruments: dict[str, Instrument],
        *,
        tls_verify: bool,
        bridge_host: str,
        bridge_port: int,
        bridge_connect_timeout_seconds: float,
        bridge_reconnect_min_seconds: int,
        bridge_reconnect_max_seconds: int,
        freq: str = "1min",
        maxsize: int = 10_000,
        bridge_client: LiveFeedConnectionPort | None = None,
    ) -> None:
        self.instruments = instruments
        # Index instruments live on prod/marketfeed/index/v1, everything else
        # on prod/marketfeed/mw/v1. Subscribing an index token to the mw feed
        # yields no ticks at all — a silent no-data failure.
        self._topics = {
            bridge_topic(i): sym
            for sym, i in instruments.items()
            if i.exchange.upper() != "INDICES"
        }
        self._index_topics = {
            bridge_topic(i): sym
            for sym, i in instruments.items()
            if i.exchange.upper() == "INDICES" and i.conid is not None
        }
        self._queues: dict[str, queue.Queue] = {}
        self._bridge: BridgeClient | LiveFeedConnectionPort | None = None
        self._owns_bridge = bridge_client is None
        self._shared_bridge = bridge_client
        self._session = session
        self._tls_verify = tls_verify
        self._bridge_host = bridge_host
        self._bridge_port = bridge_port
        self._bridge_connect_timeout_seconds = bridge_connect_timeout_seconds
        self._bridge_reconnect_min_seconds = bridge_reconnect_min_seconds
        self._bridge_reconnect_max_seconds = bridge_reconnect_max_seconds
        self._snapshots: queue.Queue = queue.Queue(maxsize=maxsize)
        self._aggregators: dict[str, TickToBarAggregator] = {}
        self._bars_by_symbol: dict[str, Bar | None] = {}
        self._last_tick: dict[str, Tick] = {}
        self._lock = threading.Lock()
        self.freq = freq
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------
    def start(self, subscribe_oi: bool = False) -> None:
        self._stop_event.clear()
        if self._owns_bridge:
            from forward.brokers.iifl.live_feed import (
                normalize_iifl_index_quote,
                normalize_iifl_market_quote,
            )

            bridge = BridgeClient(
                self._session,
                tls_verify=self._tls_verify,
                host=self._bridge_host,
                port=self._bridge_port,
                reconnect_min_seconds=self._bridge_reconnect_min_seconds,
                reconnect_max_seconds=self._bridge_reconnect_max_seconds,
                client_id=self._session.preferred_username(),
            )
            bridge.on_feed = lambda topic, packet: self._on_feed(
                topic, normalize_iifl_market_quote(packet)
            )
            bridge.on_index = lambda topic, packet: self._on_index(
                topic, normalize_iifl_index_quote(packet)
            )
            bridge.on_error = lambda code, msg: logger.error("bridge error {}: {}", code, msg)
            bridge.connect(self._bridge_connect_timeout_seconds)
        else:
            if self._shared_bridge is None:
                raise RuntimeError("shared live-feed connection is required")
            bridge = self._shared_bridge
            bridge.add_feed_listener(self._on_feed)
            bridge.add_index_listener(self._on_index)
        self._bridge = bridge

        for symbol in self.instruments:
            self._aggregators[symbol] = TickToBarAggregator(self.freq, on_bar=None)

        self._bridge.subscribe_feed(list(self._topics))
        if self._index_topics:
            self._bridge.subscribe_index(list(self._index_topics))
        if subscribe_oi:
            fno = [t for t, s in self._topics.items() if self.instruments[s].is_derivative]
            if fno:
                self._bridge.subscribe_open_interest(fno)
        if self._owns_bridge:
            self._bridge.subscribe_order_updates()
            self._bridge.subscribe_trade_updates()

    def subscribe_instruments(self, instruments: list[Instrument]) -> None:
        """Subscribe newly selected contracts while the live feed is running.

        Args:
            instruments: Contracts selected by a strategy after observing spot.

        Raises:
            RuntimeError: If the feed has not started or an instrument has no id.
        """
        if self._bridge is None:
            raise RuntimeError("live feed must be started before adding contracts")
        feed_topics: list[str] = []
        index_topics: list[str] = []
        with self._lock:
            for instrument in instruments:
                symbol = instrument.symbol
                if symbol in self.instruments:
                    continue
                self.instruments[symbol] = instrument
                self._aggregators[symbol] = TickToBarAggregator(self.freq, on_bar=None)
                self._bars_by_symbol[symbol] = None
                if instrument.exchange.upper() == "INDICES":
                    if instrument.conid is None:
                        raise ValueError(f"index {symbol} has no instrument id")
                    token = bridge_topic(instrument)
                    self._index_topics[token] = symbol
                    index_topics.append(token)
                else:
                    topic = bridge_topic(instrument)
                    self._topics[topic] = symbol
                    feed_topics.append(topic)
        if feed_topics:
            self._bridge.subscribe_feed(feed_topics)
        if index_topics:
            self._bridge.subscribe_index(index_topics)

    def stop(self) -> None:
        self._stop_event.set()
        if self._bridge and self._owns_bridge:
            self._bridge.disconnect()
        elif self._bridge:
            self._bridge.remove_feed_listener(self._on_feed)
            self._bridge.remove_index_listener(self._on_index)

    # ------------------------------------------------------------------
    def _ingest(self, symbol: str, ts, ltp: float, qty: float,
                bid=None, ask=None, bid_size=None, ask_size=None) -> None:
        tick = Tick(
            ts=ts,
            last=ltp,
            bid=bid,
            ask=ask,
            bid_size=bid_size,
            ask_size=ask_size,
            volume=qty,
        )
        aggregator = self._aggregators.get(symbol)
        if aggregator is None:
            return
        completed = aggregator.update(ts, ltp, qty)
        with self._lock:
            self._last_tick[symbol] = tick
            self._bars_by_symbol[symbol] = aggregator.current_bar()
            if completed is not None:
                self._snapshots.put(
                    MarketSnapshot(ts=completed.ts, bars={symbol: completed})
                )

    def _on_feed(self, topic: str, feed) -> None:
        symbol = self._topics.get(topic)
        if symbol is None:
            return
        self._ingest(
            symbol,
            feed.traded_at,
            feed.last,
            feed.quantity,
            bid=feed.bid,
            ask=feed.ask,
            bid_size=feed.bid_quantity,
            ask_size=feed.ask_quantity,
        )

    def _on_index(self, suffix: str, feed) -> None:
        """Index ticks arrive on a separate feed with their own suffixes."""
        symbol = self._index_topics.get(suffix)
        if symbol is None:
            # Fall back to matching by token: some suffixes carry the raw
            # instrument id rather than the subscribed form.
            for token, sym in self._index_topics.items():
                if token in suffix or suffix in token:
                    symbol = sym
                    break
        if symbol is None:
            logger.debug("index tick for unknown suffix: {}", suffix)
            return
        self._ingest(
            symbol,
            feed.traded_at,
            feed.last,
            feed.quantity,
            bid=feed.bid,
            ask=feed.ask,
        )

    def get(self, timeout: float = 1.0) -> MarketSnapshot | None:
        try:
            return self._snapshots.get(timeout=timeout)
        except queue.Empty:
            return None

    def last_tick(self, symbol: str) -> Tick | None:
        with self._lock:
            return self._last_tick.get(symbol)

    def latest(self) -> MarketSnapshot | None:
        with self._lock:
            bars = {s: b for s, b in self._bars_by_symbol.items() if b is not None}
        if not bars:
            return None
        return MarketSnapshot(ts=max(b.ts for b in bars.values()), bars=bars)
