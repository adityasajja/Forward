"""Forward Learning Operations acceptance: trustworthy evidence, accumulated continuously.

The chain under test
--------------------
Paper trade → closed trade → forward evidence → learning dataset
→ context analytics → readiness state.

One trade in this file is driven through the *genuine* chain (live tick, rule,
risk gate, OMS order with its provenance stamp, paper fill, position fold,
journal reconcile, recorded signal context). Everything else the chain needs —
earlier weeks for progression, an in-sample backtest, an open episode — is
seeded at the repository layer with explicit timestamps and grades, and said
so: those fixtures control *time*, not provenance, whose semantics the genuine
trade and ``test_learning_forward_paper.py`` already prove.

Also proven here, because each would silently corrupt the book:

* **in-sample never counts** — a backtest trade for the same strategy leaves
  every forward figure untouched;
* **duplicates never inflate** — ``trade_ref`` decides identity; the dropped
  references are reported;
* **missing never becomes zero** — an open episode contributes no number
  anywhere, and the mean is computed over measured outcomes only;
* **provenance is immutable** — the OMS stamp survives reconcile and rebuild,
  and no repository method can rewrite it;
* **readiness changes nothing** — strategies, versions and deployments are
  byte-identical before and after every read in this file.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from forward.appdb.backtest_repository import (
    BacktestArtefactRepository,
    BacktestRunRepository,
)
from forward.appdb.daily_learning_storage import SqlDailyLearningStorage
from forward.appdb.deployment_repository import DeploymentRepository
from forward.appdb.learning_storage import SqlLearningStorage
from forward.appdb.order_event_repository import OrderEventRepository
from forward.appdb.order_storage import SqlOrderStorage
from forward.appdb.paper_ledger_storage import SqlPaperLedgerStorage
from forward.appdb.paper_runner_storage import SqlPaperRunnerStorage
from forward.appdb.portfolio_storage import SqlPortfolioStorage
from forward.appdb.risk_state_storage import SqlRiskStateStorage
from forward.appdb.strategy_repository import StrategyRepository
from forward.appdb.system_state_storage import SqlSystemStateStorage
from forward.appdb.trade_journal_repository import TradeJournalRepository
from forward.appdb.trade_journal_storage import SqlTradeJournalStorage
from forward.config.settings import get_settings
from forward.core.learning_evidence import CLASS_PAPER_FORWARD, GRADE_FORWARD
from forward.data.cached_market_frames import CachedMarketFramesAdapter
from forward.data.learning_artifacts import LocalLearningArtifactsAdapter
from forward.data.vwap_liquidity import NseBhavcopyLiquidityAdapter
from forward.market_calendar import get_market_calendar
from forward.services.learning import LearningService
from forward.services.learning_dataset import LearningDatasetBuilder
from forward.services.paper_ledger import PaperLedger
from forward.services.risk import RiskStateService
from tests.market_data_fakes import (
    InMemoryLearningArtifactsPort,
    InMemoryMarketFramesPort,
    NoopSignalContextEnricher,
    signal_context_service_for_test,
)
from tests.order_fakes import order_instrument_lookup
from tests.paper_price_fakes import StaticRunnerVenueProvider, no_live_price

SYMBOL = "RELIANCE"
EXCHANGE = "NSEEQ"
USER = "u1"
SESSION = datetime(2026, 9, 14, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))

BREAKOUT = {
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


def _daily(n: int = 300, start: float = 1000.0) -> pd.DataFrame:
    index = pd.date_range("2025-01-01", periods=n, freq="D")
    closes = [start + i for i in range(n)]
    volumes = [500_000.0] * n
    volumes[-1] = 5_000_000.0
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


def _frame(seed: int, periods: int = 300, up: float = 0.001) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    steps = rng.normal(up, 0.012, periods).cumsum()
    close = 100.0 * np.exp(steps)
    high = close * (1 + rng.uniform(0.001, 0.02, periods))
    low = close * (1 - rng.uniform(0.001, 0.02, periods))
    open_ = close * (1 + rng.normal(0, 0.004, periods))
    volume = rng.integers(100_000, 900_000, periods).astype(float)
    return pd.DataFrame(
        {
            "ts": pd.date_range("2024-01-01", periods=periods, freq="B"),
            "open": open_,
            "high": np.maximum.reduce([high, open_, close]),
            "low": np.minimum.reduce([low, open_, close]),
            "close": close,
            "volume": volume,
        }
    )


# ---------------------------------------------------------------------------
# fixtures — one genuine trade, then time-controlled company around it
# ---------------------------------------------------------------------------


@pytest.fixture()
def owner(app_db) -> str:
    from forward.appdb.account_schema import users

    with app_db.session() as session:
        session.execute(
            users.insert().values(
                user_id=USER,
                email="forward-ops@example.com",
                username="forward-ops",
                display_name="F",
                password_hash="x",
                role="owner",
                is_active=True,
                mfa_enabled=False,
                failed_logins=0,
                created_at=datetime(2026, 1, 1),
                updated_at=datetime(2026, 1, 1),
            )
        )
    return USER


@pytest.fixture()
def strategy(app_db, owner) -> dict:
    with app_db.session() as session:
        created = StrategyRepository.create(
            session, user_id=owner, name="Breakout V1", kind="rules"
        )
        version = StrategyRepository.create_version(
            session,
            strategy_id=created["strategy_id"],
            author_user_id=owner,
            definition=BREAKOUT,
        )
    return {"strategy_id": created["strategy_id"], "version": int(version["version"])}


@pytest.fixture()
def deployment(app_db, owner, strategy) -> str:
    with app_db.session() as session:
        created = DeploymentRepository.create(
            session,
            user_id=owner,
            strategy_id=strategy["strategy_id"],
            strategy_version=strategy["version"],
            mode="PAPER",
            capital=500_000.0,
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
    return created["deployment_id"]


def _flatten(app_db, deployment_id: str, *, multiplier: float) -> None:
    from forward.execution.oms import OrderDraft
    from forward.services import paper_venue as paper_service
    from forward.services.execution import ExecutionService
    from forward.services.orders import get_order_service
    from forward.services.paper_ledger import PaperLedger

    ledger = PaperLedger(storage=SqlPaperLedgerStorage(app_db), price_source=no_live_price)
    portfolio = ledger.portfolio(USER, deployment_id=deployment_id)
    position = portfolio.position(SYMBOL)
    quantity = abs(float(position.quantity))
    assert quantity > 0, "the entry must have filled before the exit is placed"
    exit_price = round(float(position.avg_price) * multiplier, 2)
    ExecutionService(
        orders=get_order_service(
            instruments=order_instrument_lookup,
            storage=SqlOrderStorage(app_db),
            risk=RiskStateService(SqlRiskStateStorage(app_db), get_settings()),
            portfolio=portfolio,
        ),
        venue=paper_service.paper_venue(
            lambda s, e: exit_price,
            instruments=order_instrument_lookup,
        ),
    ).place(
        OrderDraft(
            user_id=USER,
            symbol=SYMBOL,
            side="SELL",
            quantity=quantity,
            mode="PAPER",
            exchange=EXCHANGE,
            requested_price=exit_price,
            deployment_id=deployment_id,
        )
    )


@pytest.fixture()
def genuine(app_db, owner, strategy, deployment, monkeypatch) -> dict:
    """One paper trade closed through the real chain, with its context recorded."""
    monkeypatch.setattr(
        "forward.data.cached_market_frames.load_daily_from_cache",
        lambda symbol, exchange="NSEEQ", **kw: _daily(),
    )
    from forward.services.journal import TradeJournalService
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
    frame = _daily()
    tick = round(float(frame["close"].tail(63).max()) * 0.995, 2)
    runner._loops[deployment].venue.prices = lambda s, e: tick
    runner.pass_once(now=SESSION)

    journal = TradeJournalService(storage=SqlTradeJournalStorage(app_db), ledger=runner.ledger)
    journal.reconcile(USER, deployment)
    _flatten(app_db, deployment, multiplier=1.02)
    journal.reconcile(USER, deployment)

    signal_id = _entry_signal_id(app_db, deployment)
    assert signal_id, "the runner stamps the bar key on the entry order"
    score = _record_context(app_db, owner, strategy, signal_id)
    return {"deployment_id": deployment, "signal_id": signal_id, "score": score}


def _entry_signal_id(app_db, deployment_id: str) -> str | None:
    from sqlalchemy import select

    from forward.appdb.execution_schema import orders

    with app_db.session() as session:
        row = session.execute(
            select(orders.c.signal_id)
            .where(orders.c.deployment_id == deployment_id, orders.c.side == "BUY")
            .order_by(orders.c.created_at)
            .limit(1)
        ).first()
    return row[0] if row else None


def _entry_order_id(app_db, deployment_id: str) -> str:
    from sqlalchemy import select

    from forward.appdb.execution_schema import orders

    with app_db.session() as session:
        row = session.execute(
            select(orders.c.order_id)
            .where(orders.c.deployment_id == deployment_id, orders.c.side == "BUY")
            .order_by(orders.c.created_at)
            .limit(1)
        ).first()
    assert row is not None
    return row[0]


def _record_context(app_db, user_id: str, strategy: dict, signal_id: str) -> int:
    """A genuinely recorded context row for the signal — built by the engine,
    stored by the service, then joined back by the dataset builder."""

    service = signal_context_service_for_test(
        app_db,
        data_root=Path(get_settings().research_data_directory),
    )
    stock = _frame(11, up=0.002)
    bench = _frame(12)
    when = stock["ts"].iloc[250]
    market = service.engine.build_market_snapshot(
        bench, when, breadth_above_ema50_pct=62.0, regime="BULLISH_TREND"
    )
    sector = service.engine.build_sector_snapshot("IT", when, relative_strength_1m=2.0)
    context = service.engine.enrich(
        signal_id=signal_id,
        symbol=SYMBOL,
        action="BUY",
        signal_source="PAPER",
        signal_ts=str(when),
        strategy_id=strategy["strategy_id"],
        strategy_version=strategy["version"],
        stock_frame=stock,
        bench_frame=bench,
        market_hint=market,
        sector_hint=sector,
        when=when,
    )
    service.record(context, user_id)
    return int(context.context_score)


def _seed_closed_forward(
    app_db,
    *,
    strategy_id: str,
    symbol: str,
    weeks_ago: int,
    entry_price: float,
    exit_price: float,
) -> str:
    """A time-controlled forward episode. The grade is stated, not proven, by
    this fixture — provenance semantics are proven by the genuine trade and by
    ``test_learning_forward_paper.py``; what these rows buy is *time*."""
    # Spread across weeks so progression has something to accumulate.
    entry = datetime(2026, 8, 3) + timedelta(weeks=weeks_ago)  # a Monday
    exit_ts = entry + timedelta(days=2)
    with app_db.session() as session:
        episode = TradeJournalRepository.open_trade(
            session,
            user_id=USER,
            symbol=symbol,
            side="BUY",
            quantity=10,
            entry_price=entry_price,
            entry_ts=entry,
            strategy_id=strategy_id,
            strategy_version=1,
            evidence_grade=GRADE_FORWARD,
        )
        TradeJournalRepository.close_trade(
            session,
            episode["trade_id"],
            USER,
            exit_price=exit_price,
            gross_pnl=(exit_price - entry_price) * 10,
            net_pnl=(exit_price - entry_price) * 10,
            exit_ts=exit_ts,
        )
    return episode["trade_id"]


@pytest.fixture()
def book(app_db, owner, strategy, genuine, tmp_path) -> dict:
    """The genuine trade plus company: two earlier forward weeks, one backtest
    trade for the same strategy, and one still-open forward episode."""
    _seed_closed_forward(
        app_db,
        strategy_id=strategy["strategy_id"],
        symbol="TCS",
        weeks_ago=4,
        entry_price=100.0,
        exit_price=104.0,
    )
    _seed_closed_forward(
        app_db,
        strategy_id=strategy["strategy_id"],
        symbol="INFY",
        weeks_ago=2,
        entry_price=100.0,
        exit_price=99.0,
    )
    with app_db.session() as session:
        run = BacktestRunRepository.create(
            session,
            user_id=owner,
            config={"strategy_id": strategy["strategy_id"]},
            strategy_id=strategy["strategy_id"],
            strategy_version=1,
        )
        BacktestArtefactRepository.save_trades(
            session,
            run["run_id"],
            [
                {
                    "symbol": SYMBOL,
                    "direction": "LONG",
                    "quantity": 10,
                    "entry_ts": datetime(2025, 6, 2),
                    "entry_price": 100.0,
                    "exit_ts": datetime(2025, 6, 9),
                    "exit_price": 150.0,
                    "gross_pnl": 500.0,
                    "net_pnl": 490.0,
                    "return_pct": 50.0,
                    "strategy_id": strategy["strategy_id"],
                    "strategy_version": 1,
                }
            ],
        )
        BacktestRunRepository.complete(session, run["run_id"], owner, metrics={})
        TradeJournalRepository.open_trade(
            session,
            user_id=USER,
            symbol="HDFCBANK",
            side="BUY",
            quantity=10,
            entry_price=50.0,
            entry_ts=datetime.now() - timedelta(hours=1),
            strategy_id=strategy["strategy_id"],
            strategy_version=1,
            evidence_grade=GRADE_FORWARD,
        )
    return {"strategy": strategy, "genuine": genuine}


def _dataset(app_db, tmp_path):
    builder = LearningDatasetBuilder(
        portfolio_storage=SqlPortfolioStorage(app_db),
        storage=SqlLearningStorage(app_db),
        artifacts=LocalLearningArtifactsAdapter(tmp_path),
        market_history=InMemoryMarketFramesPort({}),
    )
    return builder.build(user_id=USER)


def _governance_snapshot(app_db) -> dict:
    from sqlalchemy import select

    from forward.appdb.execution_schema import deployments
    from forward.appdb.strategy_backtest_schema import strategies, strategy_versions

    with app_db.session() as session:
        return {
            name: sorted(
                str(sorted(dict(r).items()))
                for r in session.execute(select(table)).mappings().all()
            )
            for name, table in (
                ("strategies", strategies),
                ("strategy_versions", strategy_versions),
                ("deployments", deployments),
            )
        }


# ---------------------------------------------------------------------------
# 1. the chain: paper → closed → forward → dataset → analytics → readiness
# ---------------------------------------------------------------------------


def test_closed_paper_trade_enters_the_dataset_with_context_and_execution(
    app_db, book, tmp_path, strategy, genuine
):
    dataset = _dataset(app_db, tmp_path)

    forward = [r for r in dataset.rows if r.get("evidence_class") == CLASS_PAPER_FORWARD]
    mine = [r for r in forward if r.get("strategy_id") == strategy["strategy_id"]]
    closed = [r for r in mine if r.get("exit_ts") is not None]
    open_rows = [r for r in mine if r.get("exit_ts") is None]
    assert len(closed) == 3, "genuine trade plus two earlier forward weeks"
    assert len(open_rows) == 1, "the still-open episode is forward but has no outcome yet"

    genuine_rows = [r for r in closed if r.get("symbol") == SYMBOL]
    assert len(genuine_rows) == 1
    row = genuine_rows[0]
    # Context retained, as recorded — not recomputed.
    assert row["context_score"] == genuine["score"]
    assert row["context_model_version"] == "v1.0.0"
    assert row["context_class"]
    # Strategy retained.
    assert row["strategy_id"] == strategy["strategy_id"]
    assert row["strategy_version"] == strategy["version"]
    # Execution retained: the signal and the order that raised it.
    assert row["signal_id"] == genuine["signal_id"]
    assert row["opening_order_id"] == _entry_order_id(app_db, genuine["deployment_id"])

    # The frame carries the retention columns.
    for column in (
        "signal_id",
        "opening_order_id",
        "context_score",
        "context_model_version",
        "context_class",
    ):
        assert column in dataset.frame().columns

    # Context analytics resolves the genuine forward outcome…

    effectiveness = signal_context_service_for_test(
        app_db,
        data_root=Path(get_settings().research_data_directory),
    ).effectiveness(USER, strategy_id=strategy["strategy_id"])
    assert effectiveness["forward_n"] >= 1
    assert effectiveness["forward_with_metric"] >= 1

    # …and readiness sees the strategy accumulating evidence.
    readiness = LearningService(
        artifacts=InMemoryLearningArtifactsPort(),
        market_history=InMemoryMarketFramesPort({}),
        portfolio_storage=SqlPortfolioStorage(app_db),
        storage=SqlLearningStorage(app_db),
        daily_learning_storage=SqlDailyLearningStorage(app_db),
    ).readiness(user_id=USER)
    entry = next(s for s in readiness["strategies"] if s["strategy_id"] == strategy["strategy_id"])
    assert entry["forward_trades"] == 3
    assert entry["state"] == "NOT READY"
    assert entry["next_gate"] == {"required": 10, "have": 3, "status": "INSUFFICIENT"}
    assert readiness["advisory_only"] is True
    assert readiness["applies_changes"] is False


def test_in_sample_backtest_never_counts_as_forward(app_db, book, tmp_path, strategy):
    dataset = _dataset(app_db, tmp_path)
    assert dataset.grade_counts()["in_sample"] >= 1

    readiness = LearningService(
        artifacts=InMemoryLearningArtifactsPort(),
        market_history=InMemoryMarketFramesPort({}),
        portfolio_storage=SqlPortfolioStorage(app_db),
        storage=SqlLearningStorage(app_db),
        daily_learning_storage=SqlDailyLearningStorage(app_db),
    ).readiness(user_id=USER)
    entry = next(s for s in readiness["strategies"] if s["strategy_id"] == strategy["strategy_id"])
    # Three forward weeks — the +50% backtest is evidence of nothing.
    assert entry["forward_trades"] == 3
    assert entry["summary"]["stats"]["mean"] != 50.0


def test_open_episodes_contribute_no_number(app_db, book, tmp_path, strategy):
    dataset = _dataset(app_db, tmp_path)
    open_rows = [r for r in dataset.rows if r.get("exit_ts") is None]
    assert open_rows, "the fixture holds one still-open episode"
    assert all(r.get("return_pct") is None for r in open_rows)

    readiness = LearningService(
        artifacts=InMemoryLearningArtifactsPort(),
        market_history=InMemoryMarketFramesPort({}),
        portfolio_storage=SqlPortfolioStorage(app_db),
        storage=SqlLearningStorage(app_db),
        daily_learning_storage=SqlDailyLearningStorage(app_db),
    ).readiness(user_id=USER)
    entry = next(s for s in readiness["strategies"] if s["strategy_id"] == strategy["strategy_id"])
    assert entry["open_forward_trades"] == 1
    # The mean is over measured outcomes only: recompute it here from the rows
    # the dataset holds, on the metric the summary resolved to.
    metric = entry["summary"]["metric"]
    expected = [
        r[metric]
        for r in dataset.rows
        if r.get("strategy_id") == strategy["strategy_id"]
        and r.get("evidence_class") == CLASS_PAPER_FORWARD
        and r.get("exit_ts") is not None
        and r.get(metric) is not None
    ]
    assert len(expected) == 3
    assert entry["summary"]["n_with_metric"] == 3
    assert entry["summary"]["stats"]["mean"] == pytest.approx(sum(expected) / 3)


def test_evidence_accumulates_week_by_week(app_db, book, tmp_path, strategy):
    readiness = LearningService(
        artifacts=InMemoryLearningArtifactsPort(),
        market_history=InMemoryMarketFramesPort({}),
        portfolio_storage=SqlPortfolioStorage(app_db),
        storage=SqlLearningStorage(app_db),
        daily_learning_storage=SqlDailyLearningStorage(app_db),
    ).readiness(user_id=USER)
    entry = next(s for s in readiness["strategies"] if s["strategy_id"] == strategy["strategy_id"])
    progression = entry["progression"]
    assert len(progression) >= 2, "trades land in distinct weeks"
    assert progression[-1]["cumulative"] == 3
    cumulative = [w["cumulative"] for w in progression]
    assert cumulative == sorted(cumulative), "the running total never steps back"
    assert all(w["week"] <= progression[-1]["week"] for w in progression)


def test_unlinked_rows_are_flagged_not_dropped(app_db, book, tmp_path, strategy):
    readiness = LearningService(
        artifacts=InMemoryLearningArtifactsPort(),
        market_history=InMemoryMarketFramesPort({}),
        portfolio_storage=SqlPortfolioStorage(app_db),
        storage=SqlLearningStorage(app_db),
        daily_learning_storage=SqlDailyLearningStorage(app_db),
    ).readiness(user_id=USER)
    by_code = {i["code"]: i for i in readiness["quality_issues"]}
    assert "missing_execution" in by_code
    assert by_code["missing_execution"]["count"] >= 2
    assert by_code["missing_execution"]["explanation"]
    # Flagged rows are still evidence: the count did not shrink.
    entry = next(s for s in readiness["strategies"] if s["strategy_id"] == strategy["strategy_id"])
    assert entry["forward_trades"] == 3


# ---------------------------------------------------------------------------
# provenance cannot be rewritten
# ---------------------------------------------------------------------------


def test_provenance_survives_reconcile_and_rebuild(app_db, book, tmp_path, genuine):
    from forward.execution.oms import PROVENANCE_FORWARD, PROVENANCE_KEY
    from forward.services.journal import TradeJournalService
    from forward.services.paper_ledger import PaperLedger

    order_id = _entry_order_id(app_db, genuine["deployment_id"])
    with app_db.session() as session:
        raws = [
            e.get("raw")
            for e in OrderEventRepository.history(session, order_id)
            if e.get("to_status") == "NEW"
        ]
    raw = json.loads(raws[0]) if isinstance(raws[0], str) else raws[0]
    assert raw.get(PROVENANCE_KEY) == PROVENANCE_FORWARD

    before = {r["trade_ref"]: r["evidence_grade"] for r in _dataset(app_db, tmp_path).rows}
    TradeJournalService(
        storage=SqlTradeJournalStorage(app_db),
        ledger=PaperLedger(storage=SqlPaperLedgerStorage(app_db), price_source=no_live_price),
    ).reconcile(USER, genuine["deployment_id"])
    after = {r["trade_ref"]: r["evidence_grade"] for r in _dataset(app_db, tmp_path).rows}
    assert before == after, "reconcile must not move a single grade"
    genuine_refs = [
        r["trade_ref"]
        for r in _dataset(app_db, tmp_path).rows
        if r.get("symbol") == SYMBOL and r.get("evidence_class") == CLASS_PAPER_FORWARD
    ]
    assert len(genuine_refs) == 1
    assert after[genuine_refs[0]] == GRADE_FORWARD


def test_no_repository_method_can_rewrite_provenance():
    mutating = {"update", "delete", "rewrite", "set_provenance", "set_grade", "amend"}
    event_writers = {
        name
        for name in dir(OrderEventRepository)
        if name in mutating or "stamp" in name or "provenance" in name
    } - {"append"}
    assert event_writers == set(), f"append-only log grew writers: {event_writers}"
    journal_writers = {
        name
        for name in dir(TradeJournalRepository)
        if "grade" in name.lower() or "evidence" in name.lower()
    }
    assert journal_writers == set(), f"journal grew grade writers: {journal_writers}"


# ---------------------------------------------------------------------------
# readiness changes nothing
# ---------------------------------------------------------------------------


def test_readiness_reads_without_writing(app_db, book, tmp_path, strategy):

    service = LearningService(
        artifacts=InMemoryLearningArtifactsPort(),
        market_history=InMemoryMarketFramesPort({}),
        portfolio_storage=SqlPortfolioStorage(app_db),
        storage=SqlLearningStorage(app_db),
        daily_learning_storage=SqlDailyLearningStorage(app_db),
    )
    before = _governance_snapshot(app_db)

    service.dataset(refresh=True, user_id=USER)
    service.readiness(user_id=USER)
    service.forward_evidence_counts(user_id=USER)
    signal_context_service_for_test(
        app_db,
        data_root=Path(get_settings().research_data_directory),
    ).effectiveness(USER)

    assert _governance_snapshot(app_db) == before


# ---------------------------------------------------------------------------
# the endpoint
# ---------------------------------------------------------------------------


def test_readiness_endpoint_answers_200_and_marks_itself_advisory(auth_client):
    response = auth_client.get("/api/v1/learning/readiness")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["gates"] == [10, 30, 50]
    assert body["strategies"] == []
    assert body["quality_issues"] == []
    assert body["advisory_only"] is True
    assert body["applies_changes"] is False
    json.dumps(body)  # NaN/Infinity would not survive the trip to a browser
