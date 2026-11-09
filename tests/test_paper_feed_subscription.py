"""Keep every running paper deployment connected to the live quote feed.

PaperRunner states its active symbol universe through the market-data port. The
API adapter connects that port to :class:`forward.api.stream.TickBroadcaster`.
Feed ownership stays independent from browser clients, so a dashboard closing
cannot remove symbols needed by a running paper deployment. If a fresh tick is
missing, the venue refuses to invent a fill from an old daily close.

These tests cover the broadcaster subscription contract and its runner wiring.
"""

from __future__ import annotations

import pandas as pd

from forward.api.stream import TickBroadcaster, _normalise_universe
from forward.appdb.order_storage import SqlOrderStorage
from forward.appdb.paper_ledger_storage import SqlPaperLedgerStorage
from forward.appdb.paper_runner_storage import SqlPaperRunnerStorage
from forward.appdb.portfolio_storage import SqlPortfolioStorage
from forward.appdb.risk_state_storage import SqlRiskStateStorage
from forward.appdb.system_state_storage import SqlSystemStateStorage
from forward.appdb.trade_journal_storage import SqlTradeJournalStorage
from forward.config.settings import get_settings
from forward.data.cached_market_frames import CachedMarketFramesAdapter
from forward.data.observed_bars import ObservedBars
from forward.data.vwap_liquidity import NseBhavcopyLiquidityAdapter
from forward.market_calendar import get_market_calendar
from forward.services.paper_ledger import PaperLedger
from forward.services.risk import RiskStateService
from tests.market_data_fakes import (
    InMemoryLiveMarketFeed,
    InMemoryTickCandleAggregation,
    NoopSignalContextEnricher,
)
from tests.order_fakes import order_instrument_lookup
from tests.paper_price_fakes import StaticRunnerVenueProvider, no_live_price

SYMBOL = "RELIANCE"


class RecordingMarketData:
    """Capture the market feed calls made by the paper runner."""

    def __init__(self) -> None:
        self.symbol_calls: list[dict[str, list[str]]] = []

    def ensure_symbols(self, universe: dict[str, list[str]]) -> dict[str, list[str]]:
        self.symbol_calls.append(universe)
        return {"added": [], "removed": [], "unresolved": []}

    def ensure_indices(self, indices: tuple[tuple[str, str], ...]) -> None:
        return None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _broadcaster(
    monkeypatch, *, resolve: bool = True
) -> tuple[TickBroadcaster, InMemoryLiveMarketFeed]:
    """A broadcaster with a fake bridge and a deterministic contract resolver.

    The resolver is stubbed rather than left real because resolving a symbol
    against the instrument master reads a 22,899-contract index — the subject
    here is the subscription logic, not the master.
    """
    bridge = InMemoryLiveMarketFeed()
    broadcaster = TickBroadcaster(bridge, InMemoryTickCandleAggregation(), ObservedBars())

    def stub_resolve(symbol: str, exchange: str = "NSEEQ"):
        if not resolve:
            return None
        topic = f"{exchange.lower()}/{abs(hash(symbol)) % 10_000}"
        broadcaster._symbol_to_topic[symbol.upper()] = topic
        broadcaster._topic_to_symbol[topic] = symbol.upper()
        return topic

    monkeypatch.setattr(broadcaster, "_resolve_symbol", stub_resolve)
    return broadcaster, bridge


# ---------------------------------------------------------------------------
# 1. the universe statement itself
# ---------------------------------------------------------------------------
def test_a_flat_universe_is_normalised_to_upper_case_symbols():
    assert _normalise_universe(["reliance", " tcs ", ""], "nseeq") == {"NSEEQ": {"RELIANCE", "TCS"}}


def test_a_per_exchange_universe_keeps_its_exchanges_apart():
    """A caller with deployments on two exchanges must not have them guessed."""
    assert _normalise_universe({"NSEEQ": ["INFY"], "BSEEQ": ["wipro"]}, "NSEEQ") == {
        "NSEEQ": {"INFY"},
        "BSEEQ": {"WIPRO"},
    }


def test_an_empty_universe_subscribes_nothing(monkeypatch):
    broadcaster, bridge = _broadcaster(monkeypatch)
    report = broadcaster.ensure_symbols([])
    assert report == {"added": [], "removed": [], "unresolved": []}
    assert bridge.subscribed == []


# ---------------------------------------------------------------------------
# 2. a server-side consumer gets the symbol onto the feed
# ---------------------------------------------------------------------------
def test_the_universe_is_subscribed_without_any_browser(monkeypatch):
    """The defect this whole path exists to close.

    No WebSocket is connected and no browser has ever asked for this symbol. It
    still has to reach the exchange, because a paper deployment is trading it.
    """
    broadcaster, bridge = _broadcaster(monkeypatch)

    report = broadcaster.ensure_symbols([SYMBOL], "NSEEQ")

    assert report["added"] == [SYMBOL]
    assert report["unresolved"] == []
    assert len(bridge.subscribed) == 1, "the symbol never reached the exchange"
    assert broadcaster._runner_symbols == {SYMBOL}


def test_restating_the_same_universe_does_not_resubscribe(monkeypatch):
    """The runner states its universe on every pass — once a second.

    A statement that re-sent the subscription each time would be a message a
    second per symbol for no gain.
    """
    broadcaster, bridge = _broadcaster(monkeypatch)
    broadcaster.ensure_symbols([SYMBOL])
    before = len(bridge.subscribed)

    for _ in range(5):
        report = broadcaster.ensure_symbols([SYMBOL])
        assert report["added"] == []

    assert len(bridge.subscribed) == before


def test_a_symbol_the_browser_already_carries_is_not_subscribed_twice(monkeypatch):
    """Two consumers, one exchange subscription."""
    broadcaster, bridge = _broadcaster(monkeypatch)
    # A browser is already watching it.
    broadcaster._subscribed_symbols[SYMBOL] = 1
    broadcaster._symbol_to_topic[SYMBOL] = "nseeq/2885"
    broadcaster._topic_to_symbol["nseeq/2885"] = SYMBOL

    report = broadcaster.ensure_symbols([SYMBOL])

    assert report["added"] == [SYMBOL]
    assert bridge.subscribed == [], "a duplicate subscription was sent to the exchange"


def test_a_multi_exchange_universe_is_stated_in_one_call(monkeypatch):
    broadcaster, bridge = _broadcaster(monkeypatch)
    report = broadcaster.ensure_symbols({"NSEEQ": ["INFY"], "BSEEQ": ["WIPRO"]})
    assert sorted(report["added"]) == ["INFY", "WIPRO"]
    assert len(bridge.subscribed) == 2


# ---------------------------------------------------------------------------
# 3. the two kinds of consumer cannot unsubscribe each other
# ---------------------------------------------------------------------------
def test_a_browser_disconnect_does_not_drop_a_deployments_symbol(monkeypatch):
    """Closing a dashboard tab must not stop a paper deployment being priced.

    This is the failure that would be hardest to notice: the chart stops
    updating, which nobody is there to see, and the deployment goes back to
    yesterday's close.
    """
    broadcaster, bridge = _broadcaster(monkeypatch)
    broadcaster._symbol_to_topic[SYMBOL] = "nseeq/2885"

    # A browser subscribes and then goes away.
    broadcaster._subscribed_symbols[SYMBOL] = 1
    broadcaster.ensure_symbols([SYMBOL])

    class FakeWs:
        pass

    ws = FakeWs()
    broadcaster._client_subscriptions[ws] = {SYMBOL}
    broadcaster.disconnect(ws)

    assert bridge.unsubscribed == [], (
        "the deployment's universe was unsubscribed by a browser leaving"
    )
    assert SYMBOL not in broadcaster._subscribed_symbols
    assert broadcaster._runner_symbols == {SYMBOL}


def test_a_browser_unsubscribe_does_not_drop_a_deployments_symbol(monkeypatch):
    broadcaster, bridge = _broadcaster(monkeypatch)
    broadcaster._symbol_to_topic[SYMBOL] = "nseeq/2885"

    class FakeWs:
        pass

    ws = FakeWs()
    broadcaster._client_subscriptions[ws] = {SYMBOL}
    broadcaster._subscribed_symbols[SYMBOL] = 1
    broadcaster.ensure_symbols([SYMBOL])

    import asyncio

    asyncio.run(broadcaster.unsubscribe(ws, [SYMBOL]))
    assert bridge.unsubscribed == []


def test_a_symbol_no_longer_wanted_is_taken_off_the_feed(monkeypatch):
    """A stopped deployment should stop consuming feed bandwidth."""
    broadcaster, bridge = _broadcaster(monkeypatch)
    broadcaster.ensure_symbols([SYMBOL])
    bridge.unsubscribed.clear()

    report = broadcaster.ensure_symbols([])

    assert report["removed"] == [SYMBOL]
    assert len(bridge.unsubscribed) == 1
    assert broadcaster._runner_symbols == set()


def test_a_symbol_a_browser_still_watches_is_not_taken_off(monkeypatch):
    broadcaster, bridge = _broadcaster(monkeypatch)
    broadcaster._symbol_to_topic[SYMBOL] = "nseeq/2885"
    broadcaster.ensure_symbols([SYMBOL])
    # A browser picks it up, then the deployment stops.
    broadcaster._subscribed_symbols[SYMBOL] = 1
    bridge.unsubscribed.clear()

    broadcaster.ensure_symbols([])

    assert bridge.unsubscribed == [], "the browser's subscription was cancelled"


# ---------------------------------------------------------------------------
# 4. failure is retried, not remembered as done
# ---------------------------------------------------------------------------
def test_a_statement_made_while_the_bridge_is_down_is_retried(monkeypatch):
    """The subtle one: "no session yet" must not be recorded as "subscribed".

    A deployment started before the broker session exists would otherwise be
    permanently absent from the feed while looking perfectly healthy.
    """
    broadcaster, bridge = _broadcaster(monkeypatch)
    bridge.is_connected = False
    # The feed is unavailable for this pass.
    monkeypatch.setattr(broadcaster, "_ensure_live_feed", lambda: False)

    first = broadcaster.ensure_symbols([SYMBOL])
    assert first["added"] == [SYMBOL]
    assert bridge.subscribed == [], "nothing can have reached the exchange"

    # The session arrives.
    bridge.is_connected = True
    monkeypatch.setattr(broadcaster, "_ensure_live_feed", lambda: True)

    second = broadcaster.ensure_symbols([SYMBOL])
    assert second["added"] == [SYMBOL], "the outstanding symbol was not retried"
    assert len(bridge.subscribed) == 1


def test_a_broker_error_during_subscribe_is_retried(monkeypatch):
    """A bridge that throws on subscribe must not consume the statement.

    The symbol is still wanted and still absent, so the next statement has to
    send it again — otherwise one transient MQTT error leaves a deployment off
    the feed for the rest of its life.
    """
    broadcaster, bridge = _broadcaster(monkeypatch)

    def explode(topics):
        raise RuntimeError("bridge is unhappy")

    monkeypatch.setattr(bridge, "subscribe_feed", explode)
    assert broadcaster.ensure_symbols([SYMBOL])["added"] == [SYMBOL]
    assert broadcaster._runner_acked == set()

    monkeypatch.setattr(bridge, "subscribe_feed", bridge.subscribed.extend)
    broadcaster.ensure_symbols([SYMBOL])
    assert len(bridge.subscribed) == 1, "the outstanding symbol was not retried"
    assert broadcaster._runner_acked == {SYMBOL}


def test_a_symbol_with_no_contract_is_reported_and_not_retried_every_pass(monkeypatch):
    """A symbol with no conid can never be priced from live ticks.

    It is reported once — a fact an operator can act on — rather than
    re-resolved and re-warned about on every pass forever.
    """
    broadcaster, _bridge = _broadcaster(monkeypatch, resolve=False)

    first = broadcaster.ensure_symbols([SYMBOL])
    assert first["unresolved"] == [SYMBOL]
    assert first["added"] == []

    second = broadcaster.ensure_symbols([SYMBOL])
    assert second["unresolved"] == [], "the unresolvable symbol was retried"
    assert second["added"] == []


def test_an_unresolvable_symbol_is_retried_once_it_leaves_and_returns(monkeypatch):
    """A corrected symbol list gets a fresh attempt."""
    broadcaster, _bridge = _broadcaster(monkeypatch, resolve=False)
    broadcaster.ensure_symbols([SYMBOL])

    broadcaster.ensure_symbols([])
    monkeypatch.setattr(
        broadcaster,
        "_resolve_symbol",
        lambda s, e="NSEEQ": broadcaster._symbol_to_topic.setdefault(s, "nseeq/1"),
    )
    assert broadcaster.ensure_symbols([SYMBOL])["added"] == [SYMBOL]


# ---------------------------------------------------------------------------
# 5. the runner is the caller, and it states the running universe
# ---------------------------------------------------------------------------
def test_the_runner_states_the_union_of_its_running_deployments(app_db, monkeypatch):
    """The runner is the only thing that knows the current universe."""
    from datetime import datetime as _dt

    from forward.appdb.account_schema import users
    from forward.appdb.deployment_repository import DeploymentRepository
    from forward.services.runner import PaperRunner

    monkeypatch.setattr(
        "forward.data.cached_market_frames.load_daily_from_cache",
        lambda symbol, exchange="NSEEQ", **kw: pd.DataFrame(
            columns=["ts", "open", "high", "low", "close", "volume"]
        ),
    )

    with app_db.session() as session:
        session.execute(
            users.insert().values(
                user_id="u1",
                email="f@example.com",
                username="f",
                display_name="F",
                password_hash="x",
                role="owner",
                is_active=True,
                mfa_enabled=False,
                failed_logins=0,
                created_at=_dt(2026, 1, 1),
                updated_at=_dt(2026, 1, 1),
            )
        )
        DeploymentRepository.create(
            session,
            user_id="u1",
            strategy_id="s",
            strategy_version=1,
            mode="PAPER",
            capital=100_000.0,
            status="RUNNING",
            config={
                "symbols": ["RELIANCE", "TCS"],
                "exchange": "NSEEQ",
                "timeframe": "1d",
                "max_open_positions": 10,
                "signal_basis": "closed_bar",
            },
        )
        DeploymentRepository.create(
            session,
            user_id="u1",
            strategy_id="s",
            strategy_version=1,
            mode="PAPER",
            capital=100_000.0,
            status="RUNNING",
            config={
                "symbols": ["INFY"],
                "exchange": "NSEEQ",
                "timeframe": "1d",
                "max_open_positions": 10,
                "signal_basis": "closed_bar",
            },
        )

    market_data = RecordingMarketData()

    runner = PaperRunner(
        interval_seconds=get_settings().runner_interval_seconds,
        lookback_days=get_settings().runner_lookback_days,
        minimum_sleep_seconds=get_settings().runner_min_sleep_seconds,
        market_wait_seconds=get_settings().runner_market_wait_seconds,
        symbol_retry_seconds=get_settings().deployment_symbol_retry_seconds,
        signal_context=NoopSignalContextEnricher(),
        calendar=get_market_calendar(),
        venue_provider=StaticRunnerVenueProvider(
            prices=lambda s, e: 100.0, instruments=order_instrument_lookup
        ),
        daily_history=CachedMarketFramesAdapter(),
        vwap_liquidity=NseBhavcopyLiquidityAdapter(get_settings().research_data_directory),
        sector_table={},
        instrument_lookup=order_instrument_lookup,
        journal_storage=SqlTradeJournalStorage(app_db),
        portfolio_storage=SqlPortfolioStorage(app_db),
        ledger=PaperLedger(storage=SqlPaperLedgerStorage(app_db), price_source=no_live_price),
        order_storage=SqlOrderStorage(app_db),
        runner_storage=SqlPaperRunnerStorage(app_db),
        state_storage=SqlSystemStateStorage(app_db),
        risk_state=RiskStateService(SqlRiskStateStorage(app_db), get_settings()),
        market_data=market_data,
    )
    runner.pass_once()

    assert market_data.symbol_calls, "the runner never told the feed what it needs"
    stated = market_data.symbol_calls[-1]
    assert set(stated["NSEEQ"]) == {"RELIANCE", "TCS", "INFY"}


def test_the_runner_does_not_state_a_universe_when_nothing_is_running(app_db, monkeypatch):
    from forward.services.runner import PaperRunner

    market_data = RecordingMarketData()
    PaperRunner(
        interval_seconds=get_settings().runner_interval_seconds,
        lookback_days=get_settings().runner_lookback_days,
        minimum_sleep_seconds=get_settings().runner_min_sleep_seconds,
        market_wait_seconds=get_settings().runner_market_wait_seconds,
        symbol_retry_seconds=get_settings().deployment_symbol_retry_seconds,
        signal_context=NoopSignalContextEnricher(),
        calendar=get_market_calendar(),
        venue_provider=StaticRunnerVenueProvider(
            prices=lambda s, e: 100.0, instruments=order_instrument_lookup
        ),
        daily_history=CachedMarketFramesAdapter(),
        vwap_liquidity=NseBhavcopyLiquidityAdapter(get_settings().research_data_directory),
        sector_table={},
        instrument_lookup=order_instrument_lookup,
        journal_storage=SqlTradeJournalStorage(app_db),
        portfolio_storage=SqlPortfolioStorage(app_db),
        ledger=PaperLedger(storage=SqlPaperLedgerStorage(app_db), price_source=no_live_price),
        order_storage=SqlOrderStorage(app_db),
        runner_storage=SqlPaperRunnerStorage(app_db),
        state_storage=SqlSystemStateStorage(app_db),
        risk_state=RiskStateService(SqlRiskStateStorage(app_db), get_settings()),
        market_data=market_data,
    ).pass_once()
    assert market_data.symbol_calls == []


def test_browser_subscription_leaves_event_loop_available(monkeypatch):
    import asyncio
    import threading

    broadcaster, bridge = _broadcaster(monkeypatch)
    ws = object()
    broadcaster._client_subscriptions[ws] = set()
    release = threading.Event()
    blocked = []

    def connect():
        blocked.append(not release.wait(0.5))
        return True

    monkeypatch.setattr(broadcaster, "_ensure_live_feed", connect)

    async def check():
        task = asyncio.create_task(broadcaster.subscribe(ws, [SYMBOL]))
        await asyncio.sleep(0)
        release.set()
        await task

    asyncio.run(check())
    assert blocked == [False], "broker connection blocked the server event loop"
    assert bridge.subscribed


def test_quote_reads_remain_available_during_bridge_connect(monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    broadcaster = TickBroadcaster(
        InMemoryLiveMarketFeed(), InMemoryTickCandleAggregation(), ObservedBars()
    )
    entered, release = threading.Event(), threading.Event()
    feed = InMemoryLiveMarketFeed()

    def connect(**values):
        del values
        entered.set()
        assert release.wait(5)
        return True

    monkeypatch.setattr(feed, "ensure_connected", connect)
    broadcaster = TickBroadcaster(feed, InMemoryTickCandleAggregation(), ObservedBars())
    with ThreadPoolExecutor(max_workers=2) as pool:
        connecting = pool.submit(broadcaster._ensure_live_feed)
        try:
            assert entered.wait(2)
            reader = pool.submit(broadcaster.latest_price_and_time, SYMBOL)
            assert reader.result(timeout=0.5) == (None, None)
        finally:
            release.set()
        assert connecting.result(timeout=2)
