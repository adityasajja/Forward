"""The Monday gap plan as rules a paper run can trade, and the clock it trades by."""

from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd

from forward.appdb.order_storage import SqlOrderStorage
from forward.appdb.paper_ledger_storage import SqlPaperLedgerStorage
from forward.appdb.paper_runner_storage import SqlPaperRunnerStorage
from forward.appdb.portfolio_storage import SqlPortfolioStorage
from forward.appdb.risk_state_storage import SqlRiskStateStorage
from forward.appdb.system_state_storage import SqlSystemStateStorage
from forward.appdb.trade_journal_storage import SqlTradeJournalStorage
from forward.config.settings import get_settings
from forward.data.cached_market_frames import CachedMarketFramesAdapter
from forward.data.vwap_liquidity import NseBhavcopyLiquidityAdapter
from forward.market_calendar import IST, NSEMarketCalendar, get_market_calendar
from forward.services.paper_ledger import PaperLedger
from forward.services.risk import RiskStateService
from forward.signals.models import EntryRules, ExitRules, SessionContext
from forward.signals.rules import eval_entry, eval_exit
from forward.signals.session import (
    build_context,
    is_last_session_of_week,
    prior_session,
    week_end_close,
)
from tests.market_data_fakes import NoopSignalContextEnricher
from tests.order_fakes import order_instrument_lookup
from tests.paper_price_fakes import StaticRunnerVenueProvider, no_live_price

MONDAY = date(2026, 9, 21)
FRIDAY_AFTER = date(2026, 9, 25)
FRIDAY = date(2026, 9, 18)


def _frame(prior_close: float, live: float, with_ts: bool = False) -> pd.DataFrame:
    closes = np.append(np.full(60, prior_close), live)
    frame = pd.DataFrame(
        {
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": np.full(61, 1000.0),
        }
    )
    if with_ts:
        days = pd.bdate_range(end=pd.Timestamp(FRIDAY), periods=60)
        frame["ts"] = list(days) + [pd.NaT]
    return frame


def _rules(**kw) -> EntryRules:
    base = dict(
        setup="gap_down",
        gap_down_pct=1.0,
        gap_market_min_pct=1.0,
        gap_weekday=0,
        gap_entry_minutes=15.0,
    )
    base.update(kw)
    return EntryRules(**base)


def _ctx(**kw) -> SessionContext:
    base = dict(
        today=MONDAY,
        prior_session=FRIDAY,
        open_price=98.0,
        minutes_since_open=1.0,
        market_week_pct=1.5,
    )
    base.update(kw)
    return SessionContext(**base)


def _fires(frame, rules, ctx) -> bool:
    return [s.rule for s in eval_entry("X", frame, rules, ctx)] == ["gap_down"]


def test_buys_a_stock_that_opened_more_than_a_percent_below_friday_on_a_rising_week():
    assert _fires(_frame(100, 98.5), _rules(), _ctx(open_price=98.0))


def test_a_small_gap_does_not_qualify():
    assert not _fires(_frame(100, 99.5), _rules(), _ctx(open_price=99.5))


def test_no_trade_when_the_market_did_not_rise_last_week():
    assert not _fires(_frame(100, 98.5), _rules(), _ctx(market_week_pct=0.4))
    assert not _fires(_frame(100, 98.5), _rules(), _ctx(market_week_pct=None))


def test_only_on_the_chosen_weekday():
    tuesday = date(2026, 9, 22)
    assert not _fires(_frame(100, 98.5), _rules(), _ctx(today=tuesday))


def test_the_open_must_be_the_real_one_and_recent():
    assert not _fires(_frame(100, 98.5), _rules(), _ctx(open_price=None, minutes_since_open=None))
    assert not _fires(_frame(100, 98.5), _rules(), _ctx(minutes_since_open=40.0))


def test_the_gap_is_measured_from_the_open_not_the_price_now():
    # it opened 2% down, then recovered to the old close: still a gap-down entry candidate
    assert _fires(_frame(100, 100.0), _rules(), _ctx(open_price=98.0))
    # it opened flat and later sank: not a gap
    assert not _fires(_frame(100, 97.0), _rules(), _ctx(open_price=100.0))


def test_stale_history_is_refused():
    frame = _frame(100, 98.5, with_ts=True)
    assert _fires(frame, _rules(), _ctx())
    assert not _fires(frame, _rules(), _ctx(prior_session=date(2026, 9, 17)))


def test_a_backtest_cannot_use_the_filters_it_cannot_see():
    assert eval_entry(
        "X", _frame(100, 98.0), _rules(gap_market_min_pct=None, gap_weekday=None), None
    )
    assert not eval_entry("X", _frame(100, 98.0), _rules(), None)


def test_a_strategy_without_the_setup_never_gap_trades():
    signals = eval_entry("X", _frame(100, 98.5), EntryRules(), _ctx())
    assert all(s.rule != "gap_down" for s in signals)


# ---- exits ---------------------------------------------------------------
def _exits() -> ExitRules:
    return ExitRules(
        stop_loss_pct=5.0,
        take_profit_pct=3.0,
        trailing_stop_pct=None,
        trend_sma=0,
        rsi_overbought=None,
        exit_at_week_end=True,
    )


def _names(signals):
    return [s.rule for s in signals]


def test_sells_at_the_target_and_at_the_stop():
    assert "take_profit" in _names(
        eval_exit("X", _frame(100, 103.2), 100.0, _exits(), context=_ctx())
    )
    assert "stop_loss" in _names(eval_exit("X", _frame(100, 94.9), 100.0, _exits(), context=_ctx()))
    assert _names(eval_exit("X", _frame(100, 101.0), 100.0, _exits(), context=_ctx())) == []


def test_sells_at_the_close_of_the_weeks_last_session_only():
    friday_close = _ctx(today=FRIDAY, week_end_close=True)
    assert "week_end" in _names(
        eval_exit("X", _frame(100, 101.0), 100.0, _exits(), context=friday_close)
    )
    assert "week_end" not in _names(
        eval_exit("X", _frame(100, 101.0), 100.0, _exits(), context=_ctx())
    )


def test_week_end_is_off_unless_asked_for():
    plain = ExitRules(
        stop_loss_pct=5.0,
        take_profit_pct=None,
        trailing_stop_pct=None,
        trend_sma=0,
        rsi_overbought=None,
    )
    friday_close = _ctx(today=FRIDAY, week_end_close=True)
    assert "week_end" not in _names(
        eval_exit("X", _frame(100, 101.0), 100.0, plain, context=friday_close)
    )


# ---- the calendar --------------------------------------------------------
def test_prior_session_skips_the_weekend():
    assert prior_session(MONDAY, NSEMarketCalendar()) == FRIDAY


def test_the_last_session_of_the_week_is_friday_or_the_day_before_a_holiday():
    cal = NSEMarketCalendar()
    assert is_last_session_of_week(FRIDAY, cal)
    assert not is_last_session_of_week(date(2026, 9, 17), cal)
    cal.add_holiday(FRIDAY)
    assert is_last_session_of_week(date(2026, 9, 17), cal)
    assert not is_last_session_of_week(FRIDAY, cal)


def test_week_end_close_starts_at_quarter_past_three():
    cal = NSEMarketCalendar()
    assert not week_end_close(datetime(2026, 9, 18, 15, 0, tzinfo=IST), cal)
    assert week_end_close(datetime(2026, 9, 18, 15, 16, tzinfo=IST), cal)
    assert not week_end_close(datetime(2026, 9, 17, 15, 16, tzinfo=IST), cal)


def test_the_market_reading_is_left_out_unless_asked_for():
    ctx = build_context(
        datetime(2026, 9, 21, 9, 20, tzinfo=IST),
        NSEMarketCalendar(),
        None,
        open_price=99.0,
        minutes_since_open=5.0,
    )
    assert ctx.market_week_pct is None and ctx.today == MONDAY and ctx.prior_session == FRIDAY


# ---- through the runner --------------------------------------------------
import pytest  # noqa: E402

SYMBOL = "TESTCO"
DEFINITION = {
    "rules": {
        "entry": {
            "setup": "gap_down",
            "gap_down_pct": 1.0,
            "gap_market_min_pct": 1.0,
            "gap_weekday": 0,
            "gap_entry_minutes": 15.0,
            "min_history_bars": 60,
        },
        "exit": {
            "stop_loss_pct": 5.0,
            "take_profit_pct": 3.0,
            "trailing_stop_pct": None,
            "trend_sma": 0,
            "rsi_overbought": None,
            "exit_at_week_end": True,
            "min_history_bars": 60,
        },
    },
}


def _history() -> pd.DataFrame:
    days = pd.bdate_range(end=pd.Timestamp(FRIDAY), periods=300)
    return pd.DataFrame(
        {"ts": days, "open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0, "volume": 1000.0}
    )


@pytest.fixture()
def gap_run(app_db, monkeypatch):
    from forward.appdb.account_schema import users
    from forward.appdb.deployment_repository import DeploymentRepository
    from forward.appdb.strategy_repository import StrategyRepository
    from forward.services import runner as runner_module

    monkeypatch.setattr(
        "forward.data.cached_market_frames.load_daily_from_cache",
        lambda symbol, exchange="NSEEQ", **kw: _history(),
    )
    monkeypatch.setattr(runner_module._MARKET_WEEK, "pct", lambda today, prior: 1.5)

    with app_db.session() as session:
        session.execute(
            users.insert().values(
                user_id="u_gap",
                email="gap@example.com",
                username="gap",
                display_name="Gap",
                password_hash="x",
                role="owner",
                is_active=True,
                mfa_enabled=False,
                failed_logins=0,
                created_at=datetime(2026, 1, 1),
                updated_at=datetime(2026, 1, 1),
            )
        )
        strategy = StrategyRepository.create(
            session, user_id="u_gap", name="Monday gap", kind="rules"
        )
        version = StrategyRepository.create_version(
            session,
            strategy_id=strategy["strategy_id"],
            author_user_id="u_gap",
            definition=DEFINITION,
        )
        deployment = DeploymentRepository.create(
            session,
            user_id="u_gap",
            strategy_id=strategy["strategy_id"],
            strategy_version=int(version["version"]),
            mode="PAPER",
            capital=500_000.0,
            status="RUNNING",
            config={
                "symbols": [SYMBOL],
                "exchange": "NSEEQ",
                "signal_basis": "live",
                "timeframe": "1d",
                "lookback_days": 400,
                "max_open_positions": 20,
            },
        )

    from forward.services.runner import PaperRunner

    runner = PaperRunner(
        interval_seconds=get_settings().runner_interval_seconds,
        lookback_days=get_settings().runner_lookback_days,
        minimum_sleep_seconds=get_settings().runner_min_sleep_seconds,
        market_wait_seconds=get_settings().runner_market_wait_seconds,
        symbol_retry_seconds=get_settings().deployment_symbol_retry_seconds,
        signal_context=NoopSignalContextEnricher(),
        calendar=get_market_calendar(),
        venue_provider=StaticRunnerVenueProvider(
            prices=no_live_price, instruments=order_instrument_lookup
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
    )
    runner.sync_loops()
    loop = runner._loops[deployment["deployment_id"]]
    feed: dict[str, float] = {}
    loop.venue.prices = lambda sym, exch: feed.get(sym.upper())
    yield runner, feed


def _at(day: date, hour: int, minute: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)


def test_a_gap_down_monday_is_bought_then_sold_at_the_target(gap_run):
    runner, feed = gap_run
    feed[SYMBOL] = 98.0  # the first price of the session: down 2% on Friday's close
    assert runner.pass_once(now=_at(MONDAY, 9, 16)).orders == 1

    feed[SYMBOL] = 100.5  # inside the band: nothing to do
    assert runner.pass_once(now=_at(MONDAY, 10, 0)).orders == 0

    feed[SYMBOL] = 101.1  # +3.2% on the 98.0 fill
    assert runner.pass_once(now=_at(MONDAY, 10, 30)).orders == 1
    last = runner.status()["deployments"][0]["last_signal"]
    assert last["side"] == "SELL" and last["rule"] == "take_profit"


def test_a_run_that_first_saw_the_stock_late_does_not_call_that_price_the_open(gap_run):
    runner, feed = gap_run
    feed[SYMBOL] = 98.0
    assert runner.pass_once(now=_at(MONDAY, 11, 30)).orders == 0


def test_no_gap_trade_on_a_tuesday(gap_run):
    runner, feed = gap_run
    feed[SYMBOL] = 98.0
    assert runner.pass_once(now=_at(date(2026, 9, 22), 9, 16)).orders == 0


def test_no_gap_trade_when_the_market_reading_is_missing(gap_run, monkeypatch):
    from forward.services import runner as runner_module

    monkeypatch.setattr(runner_module._MARKET_WEEK, "pct", lambda today, prior: None)
    runner, feed = gap_run
    feed[SYMBOL] = 98.0
    assert runner.pass_once(now=_at(MONDAY, 9, 16)).orders == 0


def test_a_trade_still_open_on_friday_is_sold_at_the_close(gap_run):
    runner, feed = gap_run
    feed[SYMBOL] = 98.0
    assert runner.pass_once(now=_at(MONDAY, 9, 16)).orders == 1

    feed[SYMBOL] = 99.0  # neither target nor stop
    assert runner.pass_once(now=_at(FRIDAY_AFTER, 14, 0)).orders == 0
    assert runner.pass_once(now=_at(FRIDAY_AFTER, 15, 16)).orders == 1
    last = runner.status()["deployments"][0]["last_signal"]
    assert last["side"] == "SELL" and last["rule"] == "week_end"


def test_a_full_book_is_filled_by_the_deepest_gaps_not_the_first_names(app_db, monkeypatch):
    from forward.appdb.account_schema import users
    from forward.appdb.deployment_repository import DeploymentRepository
    from forward.appdb.strategy_repository import StrategyRepository
    from forward.services import runner as runner_module
    from forward.services.runner import PaperRunner

    monkeypatch.setattr(
        "forward.data.cached_market_frames.load_daily_from_cache",
        lambda symbol, exchange="NSEEQ", **kw: _history(),
    )
    monkeypatch.setattr(runner_module._MARKET_WEEK, "pct", lambda today, prior: 1.5)
    names = ["AAA", "BBB", "CCC", "DDD"]  # list order is the reverse of gap depth
    opens = {"AAA": 98.9, "BBB": 98.0, "CCC": 96.0, "DDD": 90.0}

    with app_db.session() as session:
        session.execute(
            users.insert().values(
                user_id="u_rank",
                email="r@example.com",
                username="rank",
                display_name="Rank",
                password_hash="x",
                role="owner",
                is_active=True,
                mfa_enabled=False,
                failed_logins=0,
                created_at=datetime(2026, 1, 1),
                updated_at=datetime(2026, 1, 1),
            )
        )
        strategy = StrategyRepository.create(
            session, user_id="u_rank", name="Monday gap", kind="rules"
        )
        version = StrategyRepository.create_version(
            session,
            strategy_id=strategy["strategy_id"],
            author_user_id="u_rank",
            definition=DEFINITION,
        )
        DeploymentRepository.create(
            session,
            user_id="u_rank",
            strategy_id=strategy["strategy_id"],
            strategy_version=int(version["version"]),
            mode="PAPER",
            capital=500_000.0,
            status="RUNNING",
            config={
                "symbols": names,
                "exchange": "NSEEQ",
                "signal_basis": "live",
                "timeframe": "1d",
                "lookback_days": 400,
                "max_open_positions": 2,
            },
        )

    runner = PaperRunner(
        interval_seconds=get_settings().runner_interval_seconds,
        lookback_days=get_settings().runner_lookback_days,
        minimum_sleep_seconds=get_settings().runner_min_sleep_seconds,
        market_wait_seconds=get_settings().runner_market_wait_seconds,
        symbol_retry_seconds=get_settings().deployment_symbol_retry_seconds,
        signal_context=NoopSignalContextEnricher(),
        calendar=get_market_calendar(),
        venue_provider=StaticRunnerVenueProvider(
            prices=no_live_price, instruments=order_instrument_lookup
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
    )
    runner.sync_loops()
    loop = next(iter(runner._loops.values()))
    loop.venue.prices = lambda sym, exch: opens.get(sym.upper())

    runner.pass_once(now=_at(MONDAY, 9, 16))

    held = {p.instrument.symbol for p in loop._portfolio().positions.values() if not p.is_flat}
    assert held == {"DDD", "CCC"}


# ---- Triple RSI decides at the close -------------------------------------
from forward.signals.rules import eval_exit as _eval_exit  # noqa: E402


def _falling_uptrend() -> pd.DataFrame:
    up = np.linspace(100, 200, 260) + np.sin(np.arange(260)) * 1.5
    closes = np.concatenate([up, [198, 195, 191, 186, 180]])
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": np.full(len(closes), 1000.0),
        }
    )


def _triple(close_only: bool) -> EntryRules:
    return EntryRules(
        setup="triple_rsi", triple_rsi_trend_sma=200, min_history_bars=210, close_only=close_only
    )


def test_triple_rsi_ignores_the_mornings_price_but_acts_near_the_close():
    frame = _falling_uptrend()
    morning = SessionContext(today=MONDAY, near_close=False)
    late = SessionContext(today=MONDAY, near_close=True)
    assert eval_entry("X", frame, _triple(True), morning) == []
    assert [s.rule for s in eval_entry("X", frame, _triple(True), late)] == ["triple_rsi"]


def test_without_the_flag_or_without_a_context_the_rule_reads_the_bar_as_given():
    frame = _falling_uptrend()
    assert [
        s.rule for s in eval_entry("X", frame, _triple(False), SessionContext(today=MONDAY))
    ] == ["triple_rsi"]
    assert [s.rule for s in eval_entry("X", frame, _triple(True), None)] == [
        "triple_rsi"
    ]  # a backtest


def test_the_rsi_exit_also_waits_for_the_close():
    bounce = pd.DataFrame({"close": np.concatenate([np.linspace(100, 60, 60), [70, 80]])})
    for col in ("open", "high", "low"):
        bounce[col] = bounce["close"]
    bounce["volume"] = 1000.0
    exits = ExitRules(
        stop_loss_pct=50.0,
        take_profit_pct=None,
        trailing_stop_pct=None,
        trend_sma=0,
        rsi_overbought=50.0,
        rsi_period=5,
        close_only=True,
    )
    morning = _eval_exit(
        "X", bounce, avg_price=60.0, rules=exits, quantity=1, context=SessionContext(today=MONDAY)
    )
    late = _eval_exit(
        "X",
        bounce,
        avg_price=60.0,
        rules=exits,
        quantity=1,
        context=SessionContext(today=MONDAY, near_close=True),
    )
    assert "rsi_overbought" not in [s.rule for s in morning]
    assert "rsi_overbought" in [s.rule for s in late]


TRIPLE_DEFINITION = {
    "rules": {
        "entry": {
            "setup": "triple_rsi",
            "triple_rsi_trend_sma": 200,
            "min_history_bars": 210,
            "close_only": True,
        },
        "exit": {
            "stop_loss_pct": 8.0,
            "take_profit_pct": None,
            "trailing_stop_pct": None,
            "trend_sma": 0,
            "rsi_overbought": 50.0,
            "rsi_period": 5,
            "close_only": True,
            "min_history_bars": 60,
        },
    },
}


def test_a_triple_rsi_run_buys_at_the_close_not_on_the_mornings_price(app_db, monkeypatch):
    from forward.appdb.account_schema import users
    from forward.appdb.deployment_repository import DeploymentRepository
    from forward.appdb.strategy_repository import StrategyRepository
    from forward.services.runner import PaperRunner

    up = np.linspace(100, 200, 260) + np.sin(np.arange(260)) * 1.5
    closes = np.concatenate([up, [198, 195, 191, 186]])
    days = pd.bdate_range(end=pd.Timestamp(FRIDAY), periods=len(closes))
    history = pd.DataFrame(
        {
            "ts": days,
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": 1000.0,
        }
    )
    monkeypatch.setattr(
        "forward.data.cached_market_frames.load_daily_from_cache",
        lambda symbol, exchange="NSEEQ", **kw: history,
    )

    with app_db.session() as session:
        session.execute(
            users.insert().values(
                user_id="u_t",
                email="t@example.com",
                username="t",
                display_name="T",
                password_hash="x",
                role="owner",
                is_active=True,
                mfa_enabled=False,
                failed_logins=0,
                created_at=datetime(2026, 1, 1),
                updated_at=datetime(2026, 1, 1),
            )
        )
        strategy = StrategyRepository.create(
            session, user_id="u_t", name="Triple RSI", kind="rules"
        )
        version = StrategyRepository.create_version(
            session,
            strategy_id=strategy["strategy_id"],
            author_user_id="u_t",
            definition=TRIPLE_DEFINITION,
        )
        DeploymentRepository.create(
            session,
            user_id="u_t",
            strategy_id=strategy["strategy_id"],
            strategy_version=int(version["version"]),
            mode="PAPER",
            capital=500_000.0,
            status="RUNNING",
            config={
                "symbols": ["TESTCO"],
                "exchange": "NSEEQ",
                "signal_basis": "closed_bar",
                "timeframe": "1d",
                "lookback_days": 400,
                "max_open_positions": 20,
            },
        )
    runner = PaperRunner(
        interval_seconds=get_settings().runner_interval_seconds,
        lookback_days=get_settings().runner_lookback_days,
        minimum_sleep_seconds=get_settings().runner_min_sleep_seconds,
        market_wait_seconds=get_settings().runner_market_wait_seconds,
        symbol_retry_seconds=get_settings().deployment_symbol_retry_seconds,
        signal_context=NoopSignalContextEnricher(),
        calendar=get_market_calendar(),
        venue_provider=StaticRunnerVenueProvider(
            prices=no_live_price, instruments=order_instrument_lookup
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
    )
    runner.sync_loops()
    loop = next(iter(runner._loops.values()))
    loop.venue.prices = lambda sym, exch: 180.0  # a price that makes today the third falling day

    assert (
        runner.pass_once(now=_at(MONDAY, 9, 16)).orders == 0
    )  # the morning's price is not a close
    assert runner.pass_once(now=_at(MONDAY, 12, 0)).orders == 0
    assert runner.pass_once(now=_at(MONDAY, 15, 20)).orders == 1
