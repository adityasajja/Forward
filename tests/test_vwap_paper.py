from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from forward.api.stream import TickBroadcaster
from forward.appdb.forward_check_storage import SqlForwardCheckStorage
from forward.appdb.identity_repository import UserRepository
from forward.appdb.order_storage import SqlOrderStorage
from forward.appdb.paper_deployment_storage import SqlPaperDeploymentStorage
from forward.appdb.paper_ledger_storage import SqlPaperLedgerStorage
from forward.appdb.paper_runner_storage import SqlPaperRunnerStorage
from forward.appdb.portfolio_storage import SqlPortfolioStorage
from forward.appdb.risk_state_storage import SqlRiskStateStorage
from forward.appdb.strategy_repository import StrategyRepository
from forward.appdb.system_state_storage import SqlSystemStateStorage
from forward.appdb.trade_journal_storage import SqlTradeJournalStorage
from forward.brokers.runner_venue_provider import (
    BrokerRunnerVenueProvider,
    build_reversion_paper_venue,
)
from forward.config.settings import get_settings
from forward.core.market_models import LiveMarketQuote
from forward.core.runner_venue_ports import RunnerPaperExecutionProfile
from forward.data.cached_market_frames import CachedMarketFramesAdapter
from forward.data.observed_bars import ObservedBars
from forward.market_calendar import get_market_calendar
from forward.services.deployments import DeploymentError, DeploymentService
from forward.services.paper_ledger import PaperLedger
from forward.services.risk import RiskStateService
from forward.services.runner import PaperRunner
from forward.services.vwap_reversion import evaluate as evaluate_vwap
from forward.services.vwap_reversion import quote as vwap_quote
from forward.strategy.definition import validate_definition
from forward.strategy.strategies.vwap_reversion import CONTEXT, ENGINE
from tests.market_data_fakes import (
    InMemoryTickCandleAggregation,
    NoopSignalContextEnricher,
)
from tests.order_fakes import order_instrument_lookup
from tests.paper_price_fakes import no_live_price

UNIVERSE = {
    "index": {"name": "NIFTY 50", "topic": "nseeq/1"},
    "sectors": {"IT": {"topic": "nseeq/2", "members": ["A-EQ", "B-EQ", "C-EQ"]}},
}
DEFINITION = {
    "engine_key": ENGINE,
    "params": {
        "universe": UNIVERSE,
        "spec_version": 1,
        "bar_source": "observed_live_quotes",
        "allocation": 0.25,
        "max_positions": 2,
    },
    "context": CONTEXT,
}


class Feed:
    def __init__(self):
        self.now = pd.Timestamp("2026-10-15 11:00:01", tz="Asia/Kolkata").to_pydatetime()
        self.price = 98.0
        self.stale = False

    def latest_tick(self, symbol):
        when = pd.Timestamp(self.now) - pd.Timedelta(seconds=10 if self.stale else 0)
        return SimpleNamespace(
            ts=when.isoformat(),
            epoch=when.timestamp(),
            ltp=self.price,
            best_bid=self.price - 0.01,
            best_ask=self.price,
            close=100.0,
            open=100.0,
        )

    def latest_price_and_time(self, symbol):
        return self.price, self.now

    def observed_bars(self, now):
        times = pd.date_range("2026-10-15 09:15", periods=21, freq="5min", tz="Asia/Kolkata")
        close = np.r_[100 + 0.1 * np.sin(np.arange(19)), 97.8, 98.0]
        rows = [
            {
                "ts": t.isoformat(),
                "open": float(c - 0.1),
                "high": float(c + 0.4),
                "low": float(c - 0.4),
                "close": float(c),
                "volume": 10000.0,
            }
            for t, c in zip(times, close, strict=True)
        ]
        index = [{**r, "close": 22000.0} for r in rows]
        return {
            "day": "2026-10-15",
            "bars": {**{s: deepcopy(rows) for s in ["A-EQ", "B-EQ", "C-EQ"]}, "NIFTY 50": index},
        }


@pytest.fixture
def runtime(app_db, monkeypatch):
    with app_db.session() as session:
        user = UserRepository.create(
            session, email="vwap@test.example", username="vwap", password_hash="test", role="owner"
        )
        strategy = StrategyRepository.create(
            session, user_id=user["user_id"], name="VWAP", kind="code", engine_key=ENGINE
        )
        StrategyRepository.create_version(
            session,
            strategy_id=strategy["strategy_id"],
            author_user_id=user["user_id"],
            definition=DEFINITION,
        )
    service = DeploymentService(
        runner_interval_seconds=get_settings().runner_interval_seconds,
        runner_lookback_days=get_settings().runner_lookback_days,
        portfolio_storage=SqlPortfolioStorage(app_db),
        storage=SqlPaperDeploymentStorage(app_db),
        ledger=PaperLedger(storage=SqlPaperLedgerStorage(app_db), price_source=no_live_price),
        forward_trade_storage=SqlForwardCheckStorage(app_db),
    )
    row = service.create(
        user["user_id"], strategy_id=strategy["strategy_id"], strategy_version=1, capital=500000
    )
    row["definition"] = DEFINITION
    feed = Feed()
    monkeypatch.setattr("forward.api.stream.get_broadcaster", lambda: feed)
    monkeypatch.setattr("forward.services.runner._now_ist", lambda: feed.now)
    liquidity_data = SimpleNamespace(
        liquidity=lambda day: {
            s: {
                "adv": 1_000_000.0,
                "value": 1_000_000_000.0,
                "previous_close": 100.0,
                "through": "2026-10-14",
            }
            for s in ["A-EQ", "B-EQ", "C-EQ"]
        }
    )
    risk_state = RiskStateService(SqlRiskStateStorage(app_db), get_settings())
    runner = PaperRunner(
        interval_seconds=get_settings().runner_interval_seconds,
        lookback_days=get_settings().runner_lookback_days,
        minimum_sleep_seconds=get_settings().runner_min_sleep_seconds,
        market_wait_seconds=get_settings().runner_market_wait_seconds,
        symbol_retry_seconds=get_settings().deployment_symbol_retry_seconds,
        signal_context=NoopSignalContextEnricher(),
        daily_history=CachedMarketFramesAdapter(),
        vwap_liquidity=liquidity_data,
        sector_table={},
        instrument_lookup=order_instrument_lookup,
        journal_storage=SqlTradeJournalStorage(app_db),
        portfolio_storage=SqlPortfolioStorage(app_db),
        ledger=PaperLedger(storage=SqlPaperLedgerStorage(app_db), price_source=no_live_price),
        market_data=feed,
        order_storage=SqlOrderStorage(app_db),
        runner_storage=SqlPaperRunnerStorage(app_db),
        state_storage=SqlSystemStateStorage(app_db),
        risk_state=risk_state,
        calendar=get_market_calendar(),
        venue_provider=BrokerRunnerVenueProvider(
            broker_provider=lambda: None,
            price_source=no_live_price,
            instrument_lookup=order_instrument_lookup,
            risk_state=risk_state,
            adv_source=lambda symbol, exchange: None,
            paper_profiles={
                ENGINE: RunnerPaperExecutionProfile(
                    venue_builder=lambda paper_venue: build_reversion_paper_venue(
                        paper_venue=paper_venue,
                        market_data=feed,
                        vwap_liquidity=liquidity_data,
                        instrument_lookup=order_instrument_lookup,
                        quote_source=lambda symbol, now: vwap_quote(feed, symbol, now),
                        clock=lambda: feed.now,
                    ),
                    evaluator=lambda loop, now: evaluate_vwap(
                        loop, now, feed, liquidity_data, calendar=loop.calendar
                    ),
                    skip_history_warmup=True,
                    clock_sensitive=True,
                    signal_key_builder=None,
                    refuse_resting_fills=True,
                    cancel_resting_entries=False,
                    feed_subscriber=None,
                )
            },
        ),
    )
    loop = runner._make_loop(row)
    loop.warmup()
    return service, user, strategy, runner, loop, feed


def step(loop, feed):
    loop.bind()
    return loop.evaluate(now=feed.now)


def test_definition_and_live_refusal(runtime):
    assert validate_definition(DEFINITION)["ok"]
    service, user, strategy, _, loop, _ = runtime
    assert loop.config.timeframe == "5m"
    assert loop.config.max_open_positions == 2
    with pytest.raises(DeploymentError, match="paper-only"):
        service.create(
            user["user_id"],
            strategy_id=strategy["strategy_id"],
            strategy_version=1,
            capital=500000,
            mode="LIVE",
        )


def test_real_oms_entry_target_and_restart(runtime):
    _, _, _, runner, loop, feed = runtime
    entry = step(loop, feed)
    assert entry and entry[0]["status"] == "FILLED", (entry, loop.skipped_reason)
    assert loop._portfolio().position("A-EQ").quantity > 0
    # Reconstruct the loop from durable state, with no fresh snapshot required.
    restarted = runner._make_loop(loop.row)
    feed.price = 100.3
    feed.now += pd.Timedelta(seconds=1)
    exited = step(restarted, feed)
    assert exited and exited[0]["status"] == "FILLED", (exited, restarted.skipped_reason)
    assert restarted._portfolio().position("A-EQ").is_flat
    assert "A-EQ" in restarted._vwap_state["attempts"]


def test_two_slots_and_stale_exit(runtime):
    _, _, _, _, loop, feed = runtime
    assert step(loop, feed)
    assert step(loop, feed)
    assert not step(loop, feed)
    assert "Two position slots" in loop.skipped_reason
    feed.price = 100.3
    feed.stale = True
    assert not step(loop, feed)
    assert len([p for p in loop._portfolio().positions.values() if not p.is_flat]) == 2


def test_rejected_exit_is_retried_after_terminal_failure(runtime, monkeypatch):
    from forward.brokers.reversion_venue import ReversionPaperVenue
    from forward.execution.venue import VENUE_REJECTED, VenueOutcome

    _, _, _, _, loop, feed = runtime
    assert step(loop, feed)
    original = ReversionPaperVenue.submit
    failures = [True]

    def submit(self, draft):
        if draft.side == "SELL" and failures[0]:
            failures[0] = False
            return VenueOutcome(status=VENUE_REJECTED, reject_reason="temporary test rejection")
        return original(self, draft)

    monkeypatch.setattr(ReversionPaperVenue, "submit", submit)
    feed.price = 100.3
    first = step(loop, feed)
    assert first[0]["status"] == "REJECTED"
    feed.now += pd.Timedelta(seconds=6)
    second = step(loop, feed)
    assert second[0]["status"] == "FILLED"
    assert second[0]["order_id"] != first[0]["order_id"]
    assert loop._portfolio().position("A-EQ").is_flat


def test_loss_gate_closes_and_disables_entries(runtime):
    _, _, _, _, loop, feed = runtime
    assert step(loop, feed)
    feed.price = 90.0
    assert step(loop, feed)
    assert loop._vwap_state["loss_gate"]
    assert not step(loop, feed)


def test_partial_entry_is_cancelled_and_not_filled_later(runtime):
    _, _, _, runner, loop, feed = runtime
    runner.venue.fill_ratio = 0.5
    result = step(loop, feed)
    assert result[0]["status"] == "PARTIALLY_FILLED"
    order = loop.orders.get(result[0]["order_id"], loop.user_id)
    assert order["status"] == "CANCELLED"
    quantity = loop._portfolio().position("A-EQ").quantity
    restarted = runner._make_loop(loop.row)
    step(restarted, feed)
    assert restarted._portfolio().position("A-EQ").quantity == quantity


def test_unknown_submission_is_not_retried_after_restart(runtime, monkeypatch):
    from forward.brokers.reversion_venue import ReversionPaperVenue
    from forward.core.errors import VenueError

    _, _, _, runner, loop, feed = runtime
    calls = []

    def disconnected(self, draft):
        calls.append(draft)
        raise RuntimeError("unknown venue outcome")

    monkeypatch.setattr(ReversionPaperVenue, "submit", disconnected)
    with pytest.raises(VenueError):
        step(loop, feed)
    restarted = runner._make_loop(loop.row)
    assert not step(restarted, feed)
    assert "Unknown entry" in restarted.skipped_reason
    assert len(calls) == 1


def test_daily_attempt_limit_survives_restart(runtime):
    _, _, _, runner, loop, feed = runtime
    for _ in range(3):
        feed.price = 98.0
        assert step(loop, feed)
        feed.price = 100.3
        assert step(loop, feed)
        feed.now += pd.Timedelta(seconds=1)
    assert len(loop._vwap_state["attempts"]) == 3
    restarted = runner._make_loop(loop.row)
    feed.price = 98.0
    assert not step(restarted, feed)
    assert "Three daily entry attempts" in restarted.skipped_reason


def test_quote_bars_require_full_boundary_and_no_gaps():
    collector = ObservedBars()
    start = pd.Timestamp("2026-10-15 09:15", tz="Asia/Kolkata")
    for seconds in range(300):
        epoch = start.timestamp() + seconds
        collector.add("A-EQ", 100.0, seconds + 1, epoch, epoch)
    result = collector.snapshot(start + pd.Timedelta(minutes=5))
    assert result["bars"]["A-EQ"][0]["volume"] == 300
    assert not collector.snapshot(start + pd.Timedelta(minutes=4))["bars"]["A-EQ"]
    collector = ObservedBars()
    for seconds in [0, 1, 299]:
        epoch = start.timestamp() + seconds
        collector.add("A-EQ", 100.0, seconds + 1, epoch, epoch)
    assert not collector.snapshot(start + pd.Timedelta(minutes=5))["bars"]["A-EQ"]


def test_new_login_replaces_bridge_only_after_disconnect(monkeypatch):
    from forward.brokers.iifl.live_feed import IiflLiveFeedAdapter

    events = []
    old_session = SimpleNamespace(user_session="old")
    new_session = SimpleNamespace(user_session="new", is_expired=lambda: False)
    old = SimpleNamespace(
        session=old_session, is_connected=False, disconnect=lambda: events.append("old_stopped")
    )

    class Bridge:
        is_connected = True

        def __init__(
            self,
            session,
            *,
            tls_verify,
            host,
            port,
            reconnect_min_seconds,
            reconnect_max_seconds,
        ):
            assert events == ["old_stopped"]
            self.session = session

        def connect(self, **kwargs):
            events.append("new_connected")

    adapter = IiflLiveFeedAdapter(
        session_store=SimpleNamespace(load=lambda: new_session),
        instrument_catalog=SimpleNamespace(contract_id=lambda symbol, exchange: "1"),
        tls_verify=True,
        bridge_host="bridge.test",
        bridge_port=8883,
        bridge_connect_timeout_seconds=8,
        bridge_reconnect_min_seconds=1,
        bridge_reconnect_max_seconds=30,
    )
    adapter._bridge = old
    monkeypatch.setattr("forward.brokers.iifl.bridge.BridgeClient", Bridge)
    assert adapter.ensure_connected(
        on_feed=lambda *args: None,
        on_index=lambda *args: None,
        on_error=lambda *args: None,
        feed_topics=[],
        index_topics=[],
    )
    assert events == ["old_stopped", "new_connected"]
    assert adapter._bridge.session is new_session


def test_symbol_resolution_requires_an_exact_contract(monkeypatch):
    from tests.market_data_fakes import InMemoryLiveMarketFeed

    feed = InMemoryLiveMarketFeed({("RELIANCE-EQ", "NSEEQ"): "2885"})
    stream = TickBroadcaster(feed, InMemoryTickCandleAggregation(), ObservedBars())
    assert stream._resolve_symbol("RELIANCE-EQ") == "nseeq/2885"
    assert stream._resolve_symbol("RELIANCE") is None
    now = pd.Timestamp("2026-10-15 09:15:01", tz="Asia/Kolkata")
    monkeypatch.setattr("forward.api.stream.time.time", lambda: now.timestamp())
    feed = LiveMarketQuote(
        last=1185.0,
        close=1174.0,
        open=1185.0,
        high=1186.0,
        low=1184.0,
        traded_at=now.to_pydatetime(),
        quantity=1,
        volume=100,
        bid=1184.9,
        ask=1185.1,
    )
    stream._on_market_feed("nseeq/2885", feed)
    snap = stream.opening_snapshot("2026-10-15")["stocks"]
    assert "RELIANCE-EQ" in snap
    assert "RELIANCE" not in snap
    assert stream.latest_tick("RELIANCE-EQ").symbol == "RELIANCE-EQ"
    # Dropping a browser alias cannot unsubscribe the runner's same contract.
    stream._runner_symbols = {"RELIANCE-EQ"}
    assert stream._topic_in_use("nseeq/2885")
