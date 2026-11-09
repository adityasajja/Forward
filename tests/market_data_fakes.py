"""Small adapters used by tests that supply market frames in memory."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from forward.research.learning_paper import PaperLedger


class InMemoryLiveMarketFeed:
    """Record feed subscriptions and resolve a fixed contract map."""

    def __init__(
        self,
        contracts: Mapping[tuple[str, str], int | str] | None = None,
        *,
        is_connected: bool = True,
    ) -> None:
        """Create a live-feed test port.

        Args:
            contracts: Exact symbol and exchange pairs mapped to contract IDs.
            is_connected: Whether the simulated feed is online.
        """
        self.contracts = dict(contracts or {})
        self.is_connected = is_connected
        self._connected_once = is_connected
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        self.index_subscriptions: list[list[str]] = []
        self.feed_listener = None
        self.index_listener = None
        self.error_listener = None
        self.feed_listeners = set()
        self.index_listeners = set()

    def ensure_connected(
        self,
        *,
        on_feed,
        on_index,
        on_error,
        feed_topics: list[str],
        index_topics: list[str],
    ) -> bool:
        """Store callbacks and report the configured connection state."""
        self.feed_listener = on_feed
        self.index_listener = on_index
        self.error_listener = on_error
        if not self.is_connected:
            self._connected_once = False
        elif not self._connected_once:
            self.subscribed.extend(feed_topics)
            if index_topics:
                self.index_subscriptions.append(list(index_topics))
            self._connected_once = True
        return self.is_connected

    def resolve_contract_id(self, symbol: str, exchange: str) -> int | str:
        """Resolve an exact fixture contract."""
        return self.contracts[(symbol, exchange)]

    def subscribe_feed(self, topics: list[str]) -> None:
        """Record instrument topic subscriptions."""
        self.subscribed.extend(topics)

    def unsubscribe_feed(self, topics: list[str]) -> None:
        """Record removed instrument topic subscriptions."""
        self.unsubscribed.extend(topics)

    def subscribe_index(self, topics: list[str]) -> None:
        """Record index topic subscriptions."""
        self.index_subscriptions.append(list(topics))

    def add_feed_listener(self, listener) -> None:
        """Register one simulated instrument listener."""
        self.feed_listeners.add(listener)

    def remove_feed_listener(self, listener) -> None:
        """Remove one simulated instrument listener."""
        self.feed_listeners.discard(listener)

    def add_index_listener(self, listener) -> None:
        """Register one simulated index listener."""
        self.index_listeners.add(listener)

    def remove_index_listener(self, listener) -> None:
        """Remove one simulated index listener."""
        self.index_listeners.discard(listener)


class InMemoryTickCandleAggregation:
    """Record tick-candle requests and return configured candles."""

    def __init__(self, candles: list[dict[str, Any]] | None = None) -> None:
        self.candles = list(candles or [])
        self.calls: list[tuple[str, list[dict[str, Any]], int]] = []

    def resample_candles(
        self,
        symbol: str,
        ticks: list[dict[str, Any]],
        interval_seconds: int,
    ) -> list[dict[str, Any]]:
        """Record one aggregation request and return its configured result."""
        self.calls.append((symbol, list(ticks), interval_seconds))
        return list(self.candles)


class NoopSignalContextEnricher:
    """Ignore signal-context enrichment when a test does not cover it."""

    def enrich_live(self, **values: Any) -> None:
        """Accept a live signal without recording context.

        Args:
            **values: Signal details supplied by the paper runner.
        """
        del values

    def enrich_run(self, run_id: str, user_id: str) -> dict[str, Any]:
        """Report that the test double did not enrich a backtest.

        Args:
            run_id: Backtest run that requested enrichment.
            user_id: Account that owns the run.

        Returns:
            Empty enrichment result for the unused test path.
        """
        del run_id, user_id
        return {"recorded": 0}


class InMemoryMarketFramesPort:
    """Provide a fixed set of frames through the cached-market-data port.

    Args:
        frames: Market frames keyed by the exact symbol used by the caller.
    """

    def __init__(self, frames: Mapping[str, Any] | None = None) -> None:
        self._frames = dict(frames) if frames is not None else {}

    def frames(
        self, exchange: str, symbols: Sequence[str] | None = None
    ) -> dict[str, Any]:
        """Return all frames or the requested exact symbols.

        Args:
            exchange: Exchange segment requested by the caller.
            symbols: Exact symbols to return, or ``None`` for all frames.

        Returns:
            A new mapping of matching symbols and frames.
        """
        del exchange
        if symbols is None:
            return dict(self._frames)
        return {symbol: self._frames[symbol] for symbol in symbols if symbol in self._frames}

    def daily_frame(self, symbol: str, exchange: str) -> Any:
        """Return one exact frame.

        Args:
            symbol: Exact symbol key.
            exchange: Exchange segment requested by the caller.

        Returns:
            The matching market frame.

        Raises:
            KeyError: If the requested symbol was not supplied.
        """
        del exchange
        return self._frames[symbol]


class InMemoryLearningArtifactsPort:
    """Provide a fixed paper ledger and sector map to a learning service.

    Args:
        ledger: Parsed paper ledger for the test.
        sectors: Industry names keyed by canonical symbol.
    """

    def __init__(
        self,
        ledger: PaperLedger | None = None,
        sectors: Mapping[str, str] | None = None,
    ) -> None:
        self._ledger = ledger if ledger is not None else PaperLedger()
        self._sectors = dict(sectors) if sectors is not None else {}
        self._snapshots: dict[str, dict[str, Any]] = {}

    def paper_ledger(self) -> PaperLedger:
        """Return the configured paper ledger.

        Returns:
            The ledger supplied for this test.
        """
        return self._ledger

    def sector_table(self) -> dict[str, str]:
        """Return the configured sector map.

        Returns:
            Industry names keyed by canonical symbol.
        """
        return dict(self._sectors)

    def write_snapshot(self, payload: Mapping[str, Any]) -> dict[str, str]:
        """Keep snapshot output in memory for service tests.

        Args:
            payload: JSON-compatible learning snapshot.

        Returns:
            Stable in-memory names for both snapshot outputs.
        """
        stamp = str(payload["generated_at"]).replace(":", "").replace("-", "")
        snapshot = json.loads(json.dumps(payload, default=str))
        self._snapshots["latest"] = snapshot
        self._snapshots[stamp] = snapshot
        return {"report": f"memory://report_{stamp}", "latest": "memory://latest"}


def signal_context_service_for_test(database, *, data_root: Path):
    """Build a signal-context service with test-owned SQL storage.

    Args:
        database: Database fixture owned by the current test.
        data_root: Test-owned root for local market cache and learning files.

    Returns:
        A signal-context service isolated to the supplied database.
    """
    from forward.appdb.signal_context_storage import SqlSignalContextStorage
    from forward.data.cache_store import PanelDailyBarCache
    from forward.data.learning_artifacts import LocalLearningArtifactsAdapter
    from forward.data.market_intel_index_refresh import (
        LocalMarketIntelIndexRefreshAdapter,
    )
    from forward.data.market_intel_market_data import LocalMarketIntelDataAdapter
    from forward.data.market_intel_snapshot import PickleMarketIntelSnapshotAdapter
    from forward.data.signal_context_market_data import (
        LocalSignalContextMarketDataAdapter,
    )
    from forward.market_intel.service import MarketIntelService
    from forward.signal_context.engine import SignalContextEngine
    from forward.signal_context.service import SignalContextService

    artifacts = LocalLearningArtifactsAdapter(data_root)
    market_intel_data = LocalMarketIntelDataAdapter(
        data_root, "NSEEQ", PanelDailyBarCache()
    )

    def broker_client():
        from forward.wiring.broker_access import get_authenticated_broker_client

        return get_authenticated_broker_client()

    market_data = LocalSignalContextMarketDataAdapter(
        data_root=data_root,
        exchange="NSEEQ",
        daily_bars=PanelDailyBarCache(),
        learning_artifacts=artifacts,
    )
    return SignalContextService(
        storage=SqlSignalContextStorage(database),
        engine=SignalContextEngine(),
        market_data=market_data,
        market_intel=MarketIntelService(
            market_data=market_intel_data,
            index_refresh=LocalMarketIntelIndexRefreshAdapter(
                data_root, broker_client
            ),
            snapshot_cache=PickleMarketIntelSnapshotAdapter(
                data_root / "panel" / "market_intel.pkl"
            ),
        ),
    )
