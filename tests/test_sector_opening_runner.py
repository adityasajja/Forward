from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace

import pytest

from forward.appdb.forward_check_storage import SqlForwardCheckStorage
from forward.appdb.order_storage import SqlOrderStorage
from forward.appdb.paper_deployment_storage import SqlPaperDeploymentStorage
from forward.appdb.paper_ledger_storage import SqlPaperLedgerStorage
from forward.appdb.paper_runner_storage import SqlPaperRunnerStorage
from forward.appdb.portfolio_storage import SqlPortfolioStorage
from forward.appdb.risk_state_storage import SqlRiskStateStorage
from forward.appdb.system_state_storage import SqlSystemStateStorage
from forward.appdb.trade_journal_storage import SqlTradeJournalStorage
from forward.brokers.runner_venue_provider import (
    BrokerRunnerVenueProvider,
    build_opening_paper_venue,
)
from forward.config.settings import get_settings
from forward.core.runner_venue_ports import RunnerPaperExecutionProfile
from forward.data.cached_market_frames import CachedMarketFramesAdapter
from forward.data.vwap_liquidity import NseBhavcopyLiquidityAdapter
from forward.market_calendar import get_market_calendar
from forward.paper.config import RunnerConfig
from forward.services.risk import RiskStateService
from forward.services.sector_opening import IST, choose, evaluate, signal_key
from forward.strategy.definition import validate_definition
from forward.strategy.strategies.sector_opening import CONTEXT, ENGINE
from tests.market_data_fakes import NoopSignalContextEnricher
from tests.order_fakes import order_instrument_lookup
from tests.paper_price_fakes import no_live_price


def _runner_config(row):
    settings = get_settings()
    return RunnerConfig.from_deployment(
        row,
        platform_interval_seconds=settings.runner_interval_seconds,
        platform_lookback_days=settings.runner_lookback_days,
    )

UNIVERSE = {
    "index": {"name": "NIFTY 50", "topic": "nseeq/1"},
    "sectors": {
        "IT": {"topic": "nseeq/2", "members": ["A-EQ", "B-EQ"]},
        "BANK": {"topic": "nseeq/3", "members": ["C-EQ"]},
    },
}
DEFINITION = {
    "engine_key": ENGINE,
    "params": {"universe": UNIVERSE, "allocation": 0.98, "max_positions": 1},
    "context": CONTEXT,
}


def at(hour, minute, second=0):
    return datetime(2026, 10, 13, hour, minute, second, tzinfo=IST)


def snapshot(sign=1):
    return {
        "indices": {
            "NIFTY 50": {"ltp": 100 + sign, "prev_close": 100},
            "IT": {"ltp": 100 + 2 * sign, "prev_close": 100},
            "BANK": {"ltp": 100 + sign, "prev_close": 100},
        },
        "stocks": {
            s: {"open": 100 + n * sign, "prev_close": 100}
            for s, n in [("A-EQ", 2), ("B-EQ", 3), ("C-EQ", 1)]
        },
    }


class Feed:
    def __init__(self, sign=1):
        self.snapshot = snapshot(sign)
        self.now = at(9, 15)
        self.stale = False

    def opening_snapshot(self, day):
        return deepcopy(self.snapshot)

    def latest_tick(self, symbol):
        ts = at(9, 19) if self.stale else self.now
        return SimpleNamespace(ts=ts.isoformat(), epoch=ts.timestamp())


class Loop:
    def __init__(self, db, feed, mode="PAPER"):
        self.row = {"definition": DEFINITION, "capital": 500000, "mode": mode}
        self.deployment_id = "opening-test"
        self.user_id = "test"
        self.ledger = SimpleNamespace(db=db)
        self.state_storage = SqlSystemStateStorage(db)
        self.feed = feed
        self.positions = {}
        self.price = 100
        self.trades = []

    def _portfolio(self):
        return SimpleNamespace(positions=self.positions, equity=500000)

    def _price_with_meta(self, symbol, now):
        return self.price, now, 0

    def _trade(self, **kwargs):
        self.trades.append(kwargs)
        return kwargs


@pytest.fixture(autouse=True)
def decision_test_oms(monkeypatch):
    from forward.services.paper_intents import place_ioc

    def submit(loop, state, save, **kwargs):
        if isinstance(loop, Loop):
            return loop._trade(**{k: kwargs[k] for k in ["symbol", "side", "quantity", "reason"]})
        return place_ioc(loop, state, save, **kwargs)

    monkeypatch.setattr("forward.services.paper_intents.place_ioc", submit)


@pytest.mark.parametrize("sign,side", [(1, "BUY"), (-1, "SELL")])
def test_rank_both_directions(sign, side):
    result = choose(UNIVERSE, snapshot(sign))
    assert (result["sector"], result["symbol"], result["side"]) == ("IT", "B-EQ", side)


def test_missing_flat_and_tie():
    snap = snapshot()
    del snap["stocks"]["C-EQ"]
    assert choose(UNIVERSE, snap)["status"] == "collecting"
    snap = snapshot()
    snap["indices"]["NIFTY 50"]["ltp"] = 100
    assert choose(UNIVERSE, snap)["status"] == "skipped"
    snap = snapshot()
    snap["stocks"]["A-EQ"]["open"] = 103
    assert choose(UNIVERSE, snap)["symbol"] == "A-EQ"


def test_version_validation_and_universe():
    report = validate_definition(DEFINITION)
    assert report["ok"], report
    assert report["paper"]["resolvable"]
    assert not report["backtest"]["resolvable"]
    config = _runner_config(
        {
            "definition": DEFINITION,
            "config": {
                "symbols": ["WRONG"],
                "exchange": "NSEEQ",
                "timeframe": "1m",
                "max_open_positions": 1,
                "signal_basis": "closed_bar",
            },
        }
    )
    assert config.symbols == ("A-EQ", "B-EQ", "C-EQ")
    assert config.square_off_time == "15:20"
    bad = deepcopy(DEFINITION)
    bad["context"]["timeframe"] = "1d"
    assert not validate_definition(bad)["ok"]


def test_freeze_restart_and_one_entry(app_db):
    feed = Feed()
    loop = Loop(app_db, feed)
    assert not evaluate(loop, at(9, 15), feed)
    feed.snapshot["stocks"]["A-EQ"]["open"] = 120
    assert not evaluate(loop, at(9, 19), feed)
    # A new process loads the durable frozen B selection.
    loop = Loop(app_db, feed)
    feed.now = at(9, 20)
    orders = evaluate(loop, feed.now, feed)
    assert orders[0]["symbol"] == "B-EQ"
    assert orders[0]["quantity"] == 4900
    restarted = Loop(app_db, feed)
    assert not evaluate(restarted, at(9, 20, 1), feed)


@pytest.mark.parametrize("kind", ["missing", "late", "stale", "future", "live"])
def test_skip_incomplete_and_invalid_entry(app_db, kind):
    feed = Feed()
    loop = Loop(app_db, feed, "LIVE" if kind == "live" else "PAPER")
    if kind == "missing":
        del feed.snapshot["stocks"]["C-EQ"]
    evaluate(loop, at(9, 15), feed)
    if kind == "stale":
        feed.stale = True
    feed.now = at(9, 21) if kind == "future" else at(9, 20)
    when = at(9, 21) if kind in {"missing", "late"} else at(9, 20)
    assert not evaluate(loop, when, feed)
    assert not loop.trades


@pytest.mark.parametrize(
    "quantity,price,clock,reason",
    [
        (10, 101, (10, 0), "target"),
        (10, 99, (10, 0), "stop"),
        (-10, 99, (10, 0), "target"),
        (-10, 101, (10, 0), "stop"),
        (10, 100, (15, 20), "square-off"),
        (-10, 100, (15, 20), "square-off"),
    ],
)
def test_actual_fill_exit_and_squareoff(app_db, quantity, price, clock, reason):
    feed = Feed()
    feed.now = at(*clock)
    loop = Loop(app_db, feed)
    loop.price = price
    loop.positions = {"B-EQ": SimpleNamespace(quantity=quantity, avg_price=100, is_flat=False)}
    orders = evaluate(loop, feed.now, feed)
    assert reason in orders[0]["reason"]
    assert orders[0]["side"] == ("SELL" if quantity > 0 else "BUY")
    assert orders[0]["quantity"] == 10


def test_stale_exit_does_not_invent_fill(app_db):
    feed = Feed()
    feed.now = at(15, 20)
    feed.stale = True
    loop = Loop(app_db, feed)
    loop.positions = {"B-EQ": SimpleNamespace(quantity=10, avg_price=100, is_flat=False)}
    assert not evaluate(loop, feed.now, feed)
    assert "remains open" in loop.skipped_reason


@pytest.mark.parametrize("expired", [False, True])
def test_dated_late_entry_exception_requires_fresh_quote_and_unexpired_authorization(
    app_db, expired
):
    feed = Feed()
    loop = Loop(app_db, feed)
    evaluate(loop, at(9, 15), feed)
    state = loop._sector_state[1]
    state["entry_exception"] = {
        "day": at(9, 25).date().isoformat(),
        "authorized_by": "test-human",
        "approved_at": at(9, 21).isoformat(),
        "expires_at": at(9, 24 if expired else 30).isoformat(),
    }
    feed.now = at(9, 25)
    result = evaluate(loop, feed.now, feed)
    if expired:
        assert not result and state["status"] == "skipped"
    else:
        assert result and state["attempted"]
        assert "User-authorized late PAPER" in result[0]["reason"]


@pytest.mark.parametrize("sign", [1, -1])
@pytest.mark.parametrize("fill_ratio", [1.0, 0.5])
def test_shared_oms_fills_and_closes_both_directions(app_db, monkeypatch, sign, fill_ratio):
    from forward.appdb.identity_repository import UserRepository
    from forward.appdb.strategy_repository import StrategyRepository
    from forward.core.paper_models import TickPrice
    from forward.services.deployments import DeploymentError, DeploymentService
    from forward.services.paper_ledger import PaperLedger
    from forward.services.runner import PaperRunner

    with app_db.session() as session:
        user = UserRepository.create(
            session,
            email="opening@example.test",
            username="opening",
            password_hash="test",
            role="owner",
        )
        strategy = StrategyRepository.create(
            session, user_id=user["user_id"], name="Opening", kind="code", engine_key=ENGINE
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
    with pytest.raises(DeploymentError, match="paper-only"):
        service.create(
            user["user_id"],
            strategy_id=strategy["strategy_id"],
            strategy_version=1,
            capital=500000,
            mode="LIVE",
        )
    row = service.create(
        user["user_id"], strategy_id=strategy["strategy_id"], strategy_version=1, capital=500000
    )
    row["definition"] = DEFINITION
    feed = Feed(sign)
    monkeypatch.setattr("forward.api.stream.get_broadcaster", lambda: feed)
    price = [100.0]
    def price_source(symbol, exchange):
        return TickPrice(price[0], feed.now)
    risk_state = RiskStateService(SqlRiskStateStorage(app_db), get_settings())
    liquidity = NseBhavcopyLiquidityAdapter(get_settings().research_data_directory)
    runner = PaperRunner(
        interval_seconds=get_settings().runner_interval_seconds,
        lookback_days=get_settings().runner_lookback_days,
        minimum_sleep_seconds=get_settings().runner_min_sleep_seconds,
        market_wait_seconds=get_settings().runner_market_wait_seconds,
        symbol_retry_seconds=get_settings().deployment_symbol_retry_seconds,
        signal_context=NoopSignalContextEnricher(),
        daily_history=CachedMarketFramesAdapter(),
        vwap_liquidity=liquidity,
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
            price_source=price_source,
            instrument_lookup=order_instrument_lookup,
            risk_state=risk_state,
            adv_source=lambda symbol, exchange: None,
            paper_profiles={
                ENGINE: RunnerPaperExecutionProfile(
                    venue_builder=lambda paper_venue: build_opening_paper_venue(
                        paper_venue=paper_venue,
                        price_source=price_source,
                    ),
                    evaluator=lambda loop, now: evaluate(loop, now, feed, calendar=loop.calendar),
                    skip_history_warmup=True,
                    clock_sensitive=True,
                    signal_key_builder=signal_key,
                    refuse_resting_fills=False,
                    cancel_resting_entries=True,
                    feed_subscriber=None,
                )
            },
        ),
    )
    runner.venue.clock = lambda: feed.now
    runner.venue.fill_ratio = fill_ratio
    loop = runner._make_loop(row)
    loop.warmup()  # No historical broker calls.
    loop.bind()
    loop.evaluate(now=at(9, 15))
    feed.now = at(9, 20)
    loop.bind()
    entry = loop.evaluate(now=feed.now)
    assert entry[0]["status"] == ("FILLED" if fill_ratio == 1 else "PARTIALLY_FILLED"), entry
    if fill_ratio < 1:
        loop.poll_resting_orders(now=feed.now)
        assert not loop.orders.open_orders(loop.user_id, deployment_id=loop.deployment_id)
    runner.venue.fill_ratio = 1.0
    position = loop._portfolio().position("B-EQ")
    assert position.quantity * sign > 0
    actual = position.avg_price
    price[0] = actual * (1.011 if sign > 0 else 0.989)
    feed.now = at(10, 0)
    loop.bind()
    exit_orders = loop.evaluate(now=feed.now)
    assert exit_orders[0]["status"] == "FILLED", exit_orders
    assert loop._portfolio().position("B-EQ").is_flat
    loop.bind()
    assert not loop.evaluate(now=feed.now)


def test_first_opening_observation_and_bad_timestamps():
    from forward.api.stream import TickBroadcaster
    from forward.data.observed_bars import ObservedBars
    from tests.market_data_fakes import (
        InMemoryLiveMarketFeed,
        InMemoryTickCandleAggregation,
    )

    stream = TickBroadcaster(
        InMemoryLiveMarketFeed(), InMemoryTickCandleAggregation(), ObservedBars()
    )
    recv = at(9, 15).timestamp()
    first = {"ltp": 101, "prev_close": 100, "recv": recv}
    stream._capture_opening("indices", "IT", first)
    stream._capture_opening("indices", "IT", {**first, "ltp": 105})
    stream._capture_opening(
        "stocks",
        "A-EQ",
        {"open": 101, "prev_close": 100, "recv": recv, "ts": at(9, 14).isoformat()},
    )
    stream._capture_opening(
        "stocks", "B-EQ", {"open": 101, "prev_close": 100, "recv": recv, "ts": "invalid"}
    )
    snap = stream.opening_snapshot("2026-10-13")
    assert snap["indices"]["IT"]["ltp"] == 101
    assert not snap["stocks"]
    assert not stream.opening_snapshot("2026-10-14")["indices"]
