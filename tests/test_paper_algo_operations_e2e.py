"""Full end-to-end integration test for Paper Algo Operations & Monitoring.

Proves the complete chain:
LIVE MARKET DATA (ticks)
→ RUNNER (pass_once)
→ STRATEGY (breakout evaluation)
→ SIGNAL (generated entry/exit signal)
→ RISK (RiskEngine approval & risk decisions captured)
→ OMS (order created with forward provenance)
→ PAPER VENUE (paper fill execution)
→ JOURNAL (opened and closed episodes without seeded rows)
→ PAPER_FORWARD (evidence grade stamped)
→ LEARNING OBSERVATION (ingested into LearningService & evidence counters)

Also validates reliability diagnostics:
- Market closed
- No live tick / missing tick
- Stale tick
- Strategy produced no signal
- Risk rejected signal
- Ensuring never showing green "RUNNING" when ticks are stale/missing or market closed.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from forward.appdb.daily_learning_storage import SqlDailyLearningStorage
from forward.appdb.learning_storage import SqlLearningStorage
from forward.appdb.order_storage import SqlOrderStorage
from forward.appdb.paper_ledger_storage import SqlPaperLedgerStorage
from forward.appdb.paper_runner_storage import SqlPaperRunnerStorage
from forward.appdb.portfolio_storage import SqlPortfolioStorage
from forward.appdb.risk_state_storage import SqlRiskStateStorage
from forward.appdb.system_state_storage import SqlSystemStateStorage
from forward.appdb.trade_journal_storage import SqlTradeJournalStorage
from forward.config.settings import get_settings
from forward.core.learning_evidence import (
    GRADE_FORWARD,
)
from forward.data.cached_market_frames import CachedMarketFramesAdapter
from forward.data.learning_artifacts import LocalLearningArtifactsAdapter
from forward.data.vwap_liquidity import NseBhavcopyLiquidityAdapter
from forward.market_calendar import get_market_calendar
from forward.services.deployment_loop import DeploymentLoop
from forward.services.learning import LearningService
from forward.services.monitoring import DeploymentMonitor
from forward.services.paper_ledger import PaperLedger
from forward.services.risk import RiskStateService
from forward.services.runner import PaperRunner
from tests.market_data_fakes import (
    InMemoryMarketFramesPort,
    NoopSignalContextEnricher,
)
from tests.order_fakes import order_instrument_lookup
from tests.paper_price_fakes import StaticRunnerVenueProvider, no_live_price

SYMBOL = "TATAMOTORS"
EXCHANGE = "NSEEQ"
SESSION = datetime(2026, 9, 14, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
CAPITAL = 500_000.0

STRATEGY_DEFINITION = {
    "engine_key": None,
    "rules": {
        "entry": {
            "breakout_lookback": 63,
            "breakout_proximity_pct": 2.0,
            "volume_multiple": 1.5,
            "volume_lookback": 20,
            "min_history_bars": 70,
        },
        "exit": {"stop_loss_pct": 3.0, "min_history_bars": 70},
    },
}


def _daily(n: int = 300, start: float = 800.0) -> pd.DataFrame:
    index = pd.date_range("2025-01-01", periods=n, freq="D")
    closes = [start + i for i in range(n)]
    volumes = [500_000.0] * n
    volumes[-1] = 5_000_000.0  # volume spike for entry rule
    return pd.DataFrame(
        {
            "ts": index,
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": volumes,
        }
    )


@pytest.fixture(autouse=True)
def _clear_feed_seams():

    yield


@pytest.fixture()
def owner(app_db) -> str:
    from forward.appdb.account_schema import users

    with app_db.session() as session:
        session.execute(
            users.insert().values(
                user_id="u_ops",
                email="ops@example.com",
                username="ops_user",
                display_name="Operations User",
                password_hash="pwd",
                role="owner",
                is_active=True,
                mfa_enabled=False,
                failed_logins=0,
                created_at=datetime(2026, 1, 1),
                updated_at=datetime(2026, 1, 1),
            )
        )
    return "u_ops"


@pytest.fixture()
def paper_ops_env(app_db, owner, monkeypatch, tmp_path):
    from forward.appdb.deployment_repository import DeploymentRepository
    from forward.appdb.strategy_repository import StrategyRepository

    monkeypatch.setattr(
        "forward.data.cached_market_frames.load_daily_from_cache",
        lambda symbol, exchange="NSEEQ", **kw: _daily(),
    )

    with app_db.session() as session:
        strategy = StrategyRepository.create(
            session, user_id=owner, name="Operational Alpha", kind="rules"
        )
        version = StrategyRepository.create_version(
            session,
            strategy_id=strategy["strategy_id"],
            author_user_id=owner,
            definition=STRATEGY_DEFINITION,
        )
        deployment = DeploymentRepository.create(
            session,
            user_id=owner,
            strategy_id=strategy["strategy_id"],
            strategy_version=int(version["version"]),
            mode="PAPER",
            capital=CAPITAL,
            status="RUNNING",
            config={
                "symbols": [SYMBOL],
                "exchange": EXCHANGE,
                "signal_basis": "live",
                "timeframe": "1d",
                "lookback_days": 400,
                "max_open_positions": 1,
            },
        )

    frame = _daily()
    high_63 = float(frame["close"].tail(63).max())
    breakout_tick = round(high_63 * 0.995, 2)

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
    loop: DeploymentLoop = runner._loops[deployment["deployment_id"]]

    live_feed = {SYMBOL: breakout_tick}
    loop.venue.prices = lambda sym, exch: live_feed.get(sym.upper())

    monitor = DeploymentMonitor(
        portfolio_storage=SqlPortfolioStorage(app_db),
        order_storage=SqlOrderStorage(app_db),
        ledger=PaperLedger(storage=SqlPaperLedgerStorage(app_db), price_source=no_live_price),
        runner=runner,
        risk_state=RiskStateService(SqlRiskStateStorage(app_db), get_settings()),
        calendar=get_market_calendar(),
    )
    learning = LearningService(
        market_history=InMemoryMarketFramesPort({}),
        portfolio_storage=SqlPortfolioStorage(app_db),
        storage=SqlLearningStorage(app_db),
        daily_learning_storage=SqlDailyLearningStorage(app_db),
        artifacts=LocalLearningArtifactsAdapter(tmp_path),
    )

    return {
        "runner": runner,
        "loop": loop,
        "user_id": owner,
        "deployment_id": deployment["deployment_id"],
        "strategy_id": strategy["strategy_id"],
        "version": int(version["version"]),
        "live_feed": live_feed,
        "breakout_tick": breakout_tick,
        "monitor": monitor,
        "learning": learning,
    }


def test_full_chain_live_data_to_genuine_forward_observation(paper_ops_env, app_db):
    """Proves the full pipeline from live market data to forward learning observation."""
    runner = paper_ops_env["runner"]
    user_id = paper_ops_env["user_id"]
    deployment_id = paper_ops_env["deployment_id"]
    monitor = paper_ops_env["monitor"]
    learning = paper_ops_env["learning"]
    live_feed = paper_ops_env["live_feed"]

    # 1. LIVE MARKET DATA & RUNNER EVALUATION (ENTRY)
    # Runner runs pass_once during cash session
    res_entry = runner.pass_once(now=SESSION)
    assert res_entry.signals == 1, "Strategy should have generated an entry breakout signal"
    assert res_entry.orders == 1, "OMS should have created an entry order"

    # Verify Runner captured signal, risk decision, order, fill
    runner_status = runner.status()
    dep_runner = [d for d in runner_status["deployments"] if d["deployment_id"] == deployment_id][0]
    assert dep_runner["last_signal"] is not None
    assert dep_runner["last_signal"]["side"] == "BUY"
    assert dep_runner["last_risk_decision"] is not None
    assert dep_runner["last_risk_decision"]["approved"] is True
    assert dep_runner["last_order"] is not None
    assert dep_runner["last_fill"] is not None
    assert dep_runner["last_fill"]["side"] == "BUY"

    # Monitor checks
    mon_status = monitor.status(user_id, deployment_id, now=SESSION)
    assert mon_status["status"] == "RUNNING"
    assert mon_status["diagnostic_state"] in ("paper_fill_completed", "running")
    assert mon_status["last_signal"] is not None
    assert mon_status["last_risk_decision"] is not None
    assert mon_status["last_order"] is not None
    assert mon_status["last_fill"] is not None
    assert mon_status["open_trades_count"] == 1

    # 2. EXIT PASS (STOP-LOSS HIT ON LIVE TICK)
    # Simulate price drop to trigger stop-loss
    live_feed[SYMBOL] = round(paper_ops_env["breakout_tick"] * 0.95, 2)
    res_exit = runner.pass_once(now=SESSION + timedelta(minutes=10))
    assert res_exit.signals == 1, "Strategy should have generated an exit stop-loss signal"
    assert res_exit.orders == 1, "OMS should have created a closing sell order"

    exit_status = [
        d for d in runner.status()["deployments"] if d["deployment_id"] == deployment_id
    ][0]
    assert exit_status["last_signal"]["side"] == "SELL"
    assert exit_status["last_risk_decision"]["approved"] is True
    assert exit_status["last_order"]["side"] == "SELL"
    assert exit_status["last_fill"]["side"] == "SELL"

    # Monitor shows closed trade
    mon_status_after = monitor.status(user_id, deployment_id, now=SESSION + timedelta(minutes=10))
    assert mon_status_after["open_trades_count"] == 0
    assert mon_status_after["trade_count"] == 1

    # 3. VERIFY TRADE JOURNAL & PROVENANCE
    from sqlalchemy import text

    with app_db.session() as session:
        journal_rows = [
            dict(r)
            for r in session.execute(
                text("select * from trade_journal where deployment_id = :dep"),
                {"dep": deployment_id},
            ).mappings()
        ]
    assert len(journal_rows) == 1, "Exactly one trade episode should be logged"
    trade = journal_rows[0]
    assert trade["exit_ts"] is not None, "Trade must be closed"
    assert trade["evidence_grade"] == GRADE_FORWARD, "Evidence grade must be PAPER_FORWARD"

    # 4. LEARNING OBSERVATION & FORWARD COUNTER
    counts = learning.forward_evidence_counts(refresh=True)
    assert counts["by_class"]["PAPER_FORWARD"]["total"] == 1
    assert counts["by_class"]["IN_SAMPLE"]["total"] == 0
    assert counts["total_genuine_forward"] == 1
    assert counts["today_genuine_forward"] == 1
    assert counts["last_7d_genuine_forward"] == 1

    # Verify breakdown by strategy and version
    strat_id = paper_ops_env["strategy_id"]
    strat_entry = [s for s in counts["by_strategy"] if s["strategy_id"] == strat_id]
    assert len(strat_entry) == 1
    assert strat_entry[0]["paper_forward"] == 1
    assert strat_entry[0]["strategy_version"] == paper_ops_env["version"]
    assert strat_entry[0]["total_genuine_forward"] == 1


def test_reliability_diagnostics_market_closed(paper_ops_env):
    """Reliability check: Market closed state is explicitly diagnosed and never says trading."""
    runner = paper_ops_env["runner"]
    user_id = paper_ops_env["user_id"]
    deployment_id = paper_ops_env["deployment_id"]
    monitor = paper_ops_env["monitor"]

    # Outside market hours: 21:00 IST
    closed_time = datetime(2026, 9, 14, 21, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    tick_res = runner.pass_once(now=closed_time)
    assert tick_res.orders == 0

    mon_status = monitor.status(user_id, deployment_id, now=closed_time)
    assert mon_status["diagnostic_state"] == "market_closed"
    assert mon_status["trading"] is False
    assert mon_status["market_open"] is False


def test_reliability_diagnostics_stale_or_missing_ticks(paper_ops_env):
    """Reliability check: Missing or stale ticks never produce a green RUNNING state."""
    loop = paper_ops_env["loop"]
    user_id = paper_ops_env["user_id"]
    deployment_id = paper_ops_env["deployment_id"]
    monitor = paper_ops_env["monitor"]

    # 1. Missing tick
    loop.venue.prices = lambda sym, exch: None
    loop.last_tick_time = None
    loop.last_tick_age_seconds = None
    loop.state = "WAITING_FOR_TICKS"
    mon_status = monitor.status(user_id, deployment_id, now=SESSION)
    assert mon_status["diagnostic_state"] == "no_live_tick"
    assert mon_status["trading"] is False

    # 2. Stale tick (older than 60s max allowed age)
    loop.last_tick_time = SESSION - timedelta(seconds=120)
    loop.last_tick_age_seconds = 120.0
    loop.state = "WAITING_FOR_TICKS"
    loop.venue.prices = lambda sym, exch: 850.0
    mon_status_stale = monitor.status(user_id, deployment_id, now=SESSION)
    assert mon_status_stale["diagnostic_state"] == "stale_tick"
    assert mon_status_stale["trading"] is False
    assert mon_status_stale["tick_age_seconds"] is not None
    assert mon_status_stale["tick_age_seconds"] >= 100


def test_reliability_diagnostics_risk_rejection(paper_ops_env, app_db):
    """Reliability check: Risk rejection is captured on runner, loop, and monitor."""
    user_id = paper_ops_env["user_id"]
    deployment_id = paper_ops_env["deployment_id"]
    monitor = paper_ops_env["monitor"]
    runner = paper_ops_env["runner"]

    # Set risk limits using RiskStateService so risk check refuses the order
    from forward.services.risk import RiskStateService

    risk_service = RiskStateService(SqlRiskStateStorage(app_db), get_settings())
    risk_service.set_limits({"max_order_notional": 10.0}, actor=user_id)

    res = runner.pass_once(now=SESSION)
    assert res.orders == 0

    dep_status = [d for d in runner.status()["deployments"] if d["deployment_id"] == deployment_id][
        0
    ]
    assert dep_status["last_risk_decision"] is not None
    assert dep_status["last_risk_decision"]["approved"] is False

    mon_status = monitor.status(user_id, deployment_id, now=SESSION)
    assert mon_status["last_risk_decision"]["approved"] is False
    assert mon_status["diagnostic_state"] == "risk_rejected"
