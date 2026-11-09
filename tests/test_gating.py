"""Readiness gates that decide.

Paper creation must never be gated — evidence has to come from somewhere, and
refusing first runs would make learning impossible by construction. LIVE
creation requires fifty effective forward trades, counted with clustering so
one crowded minute cannot clear the floor. Each test pins one side of that
contract.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest

from forward.appdb.engine import AppDatabase
from forward.appdb.forward_check_storage import SqlForwardCheckStorage
from forward.appdb.identity_repository import UserRepository
from forward.appdb.paper_deployment_storage import SqlPaperDeploymentStorage
from forward.appdb.paper_ledger_storage import SqlPaperLedgerStorage
from forward.appdb.portfolio_storage import SqlPortfolioStorage
from forward.config.settings import get_settings
from forward.services.gating import evaluate_gate
from tests.paper_price_fakes import no_live_price


@pytest.fixture
def gate_user(app_db: AppDatabase) -> dict[str, Any]:
    with app_db.session() as session:
        user = UserRepository.create(
            session,
            username="gatekeeper",
            email="gate@example.com",
            role="trader",
            password_hash="test_password_hash",
        )
        return dict(user)


def _seed(
    app_db: AppDatabase,
    user_id: str,
    strategy_id: str,
    stamps: list[datetime],
) -> None:
    """One PAPER deployment plus one journal row per stamp, all graded forward."""
    from forward.appdb.execution_schema import deployments
    from forward.appdb.trade_schema import trade_journal

    now = datetime(2026, 9, 28, 12, 0)
    with app_db.session() as session:
        session.execute(
            deployments.insert().values(
                deployment_id="dep-gate-1",
                user_id=user_id,
                strategy_id=strategy_id,
                strategy_version=1,
                mode="PAPER",
                status="RUNNING",
                capital=100000.0,
                created_at=now,
                updated_at=now,
            )
        )
        for index, stamp in enumerate(stamps):
            session.execute(
                trade_journal.insert().values(
                    trade_id=f"gate-t{index}",
                    user_id=user_id,
                    deployment_id="dep-gate-1",
                    strategy_id=strategy_id,
                    strategy_version=1,
                    symbol="RELIANCE",
                    side="BUY",
                    quantity=10.0,
                    entry_ts=stamp,
                    exit_ts=stamp + timedelta(minutes=5),
                    entry_price=100.0,
                    exit_price=101.0,
                    gross_pnl=10.0,
                    net_pnl=8.0,
                    evidence_grade="forward",
                    created_at=now,
                )
            )
        session.commit()


def test_no_rows_is_allow_not_failure(app_db: AppDatabase, gate_user: dict[str, Any]) -> None:
    """A strategy that never traded has nothing to judge; judging it would
    block every first run."""
    decision = evaluate_gate(
        "strat-new", 1, user_id=gate_user["user_id"], storage=SqlForwardCheckStorage(app_db)
    )
    assert decision["action"] == "allow"
    assert decision["state"] == "UNEVALUATED"


def test_one_crowded_minute_holds(app_db: AppDatabase, gate_user: dict[str, Any]) -> None:
    """Fourteen fills in one minute: n=14, effective one, hold with the
    numbers stated."""
    base = datetime(2026, 9, 28, 4, 15)
    _seed(
        app_db,
        gate_user["user_id"],
        "strat-crowd",
        [base + timedelta(seconds=s) for s in range(14)],
    )
    decision = evaluate_gate(
        "strat-crowd", 1, user_id=gate_user["user_id"], storage=SqlForwardCheckStorage(app_db)
    )
    assert decision["action"] == "hold"
    assert decision["n"] == 14
    assert decision["n_effective"] == 1.0
    assert decision["clusters"] == 1
    assert decision["next_gate"] == 10


def test_fifty_spread_sessions_allow(app_db: AppDatabase, gate_user: dict[str, Any]) -> None:
    """Fifty independent daily sessions clear the floor."""
    base = datetime(2026, 6, 1, 4, 15)
    _seed(
        app_db,
        gate_user["user_id"],
        "strat-proven",
        [base + timedelta(days=d) for d in range(55)],
    )
    decision = evaluate_gate(
        "strat-proven", 1, user_id=gate_user["user_id"], storage=SqlForwardCheckStorage(app_db)
    )
    assert decision["action"] == "allow"
    assert decision["state"] == "ANALYSIS READY"
    assert decision["n_effective"] == 55.0


def test_live_creation_refuses_while_paper_passes(
    app_db: AppDatabase, gate_user: dict[str, Any]
) -> None:
    """The enforcement point: LIVE raises with an actionable reason; the
    gate itself is what PAPER creation never consults."""
    from forward.services.deployments import DeploymentError, DeploymentService
    from forward.services.paper_ledger import PaperLedger

    base = datetime(2026, 9, 28, 4, 15)
    _seed(
        app_db,
        gate_user["user_id"],
        "strat-young",
        [base + timedelta(seconds=s) for s in range(4)],
    )
    svc = DeploymentService(
        runner_interval_seconds=get_settings().runner_interval_seconds,
        runner_lookback_days=get_settings().runner_lookback_days,
        portfolio_storage=SqlPortfolioStorage(app_db),
        storage=SqlPaperDeploymentStorage(app_db),
        ledger=PaperLedger(storage=SqlPaperLedgerStorage(app_db), price_source=no_live_price),
        forward_trade_storage=SqlForwardCheckStorage(app_db),
    )
    with pytest.raises(DeploymentError) as exc:
        svc.create(
            gate_user["user_id"],
            strategy_id="strat-young",
            strategy_version=1,
            capital=100000.0,
            mode="LIVE",
        )
    assert exc.value.code == "unproven_strategy"
    assert "effective" in str(exc.value).lower()
