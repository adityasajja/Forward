"""Session and completed-candle behaviour, using recorded broker reports only."""


from datetime import datetime, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from forward.appdb.order_storage import SqlOrderStorage
from forward.appdb.risk_state_storage import SqlRiskStateStorage
from forward.config.settings import get_settings
from forward.core.enums import OrderStatus
from forward.core.instrument_models import Instrument
from forward.execution.oms import RiskDecision
from forward.services.orders import OrderService
from forward.services.risk import RiskStateService
from test_paper_runner import SESSION, SYMBOL, _loop
from test_paper_runner import deployment as deployment
from test_paper_runner import wired as wired


def candles(start="2026-09-14 09:15", n=10):
    return pd.DataFrame(
        {
            "ts": pd.date_range(start, periods=n, freq="5min"),
            "open": [1000.0] * n,
            "high": [1001.0] * n,
            "low": [999.0] * n,
            "close": [1000.0] * n,
            "volume": [100.0] * n,
        }
    )


def test_forming_candle_is_excluded_and_missing_completed_bar_blocks_entry(
    wired, deployment, monkeypatch
):
    runner, _ = wired()
    loop = _loop(runner, deployment)
    loop.bind()
    loop.config.timeframe = "5m"
    loop.config.signal_basis = "closed_bar"
    loop.frames[SYMBOL] = candles()  # Includes 10:00, still forming.
    seen = []
    monkeypatch.setattr(
        "forward.signals.rules.eval_entry",
        lambda symbol, frame, rules, context: seen.append(frame.copy()) or [],
    )
    loop.evaluate(now=SESSION)
    assert seen[-1]["ts"].iloc[-1] == pd.Timestamp("2026-09-14 09:55")
    assert loop.intraday_bar[SYMBOL]["start"].hour == 10
    seen.clear()
    loop.frames[SYMBOL] = candles(n=8)  # Latest completed candle missing.
    loop.intraday_bar.clear()
    loop.evaluate(now=SESSION)
    assert not seen
    assert "history is behind" in loop.skipped_reason


def test_exchange_candles_refresh_once_per_boundary_and_keep_volume(wired, deployment):
    runner, _ = wired()
    loop = _loop(runner, deployment)
    loop.config.timeframe = "5m"
    calls = []
    loop.intraday_loaders[SYMBOL] = lambda: calls.append(1) or candles()
    frame = candles()
    closed, behind = loop._completed_intraday_frame(SYMBOL, frame, SESSION)
    assert not behind and closed.volume.iloc[-1] == 100
    loop._completed_intraday_frame(SYMBOL, frame, SESSION + timedelta(seconds=30))
    assert len(calls) == 1
    loop._completed_intraday_frame(SYMBOL, frame, SESSION + timedelta(minutes=5))
    assert len(calls) == 2


class RecordingBroker:
    def __init__(self):
        self.sent = []
        self.reports = []
        self.cancelled = []
        self.master = SimpleNamespace(
            find=lambda symbol, exchange: Instrument(symbol=symbol, exchange=exchange)
        )

    def positions(self):
        return []

    def place_order(self, order):
        order.broker_order_id = f"broker-{len(self.sent)}"
        order.status = OrderStatus.SUBMITTED
        self.sent.append(order)
        self.reports.append(order)
        return order

    def open_orders(self):
        return self.reports

    def cancel_order(self, broker_id):
        self.cancelled.append(broker_id)
        return True  # Request accepted, not a cancellation report.


def live_loop(wired, deployment, app_db, monkeypatch):
    runner, _ = wired()
    monkeypatch.setattr(
        "forward.services.deployment_loop.DeploymentLoop._record_signal_context",
        lambda *a, **kw: None,
    )
    broker = RecordingBroker()
    runner._broker_provider = lambda: broker
    # Only the risk decision is stubbed: OMS, mode isolation, reporting, fills,
    # cancellation and the evaluator use the production path.
    monkeypatch.setattr(
        "forward.services.orders.get_order_service",
        lambda **kw: OrderService(storage=SqlOrderStorage(app_db), risk_gate=lambda d: RiskDecision.ok()),
    )
    loop = _loop(runner, deployment)
    loop.row["mode"] = "LIVE"
    loop.bind()
    loop.config.entry_cutoff = "14:30"
    loop.config.square_off_time = "15:00"
    RiskStateService(SqlRiskStateStorage(app_db), get_settings()).set_execution_mode("live", reason="simulated test", actor="u1")
    return runner, loop, broker


def test_live_fill_confirmation_duplicate_guard_and_square_off(
    wired, deployment, app_db, monkeypatch
):
    runner, loop, broker = live_loop(wired, deployment, app_db, monkeypatch)
    assert loop.evaluate(now=SESSION)
    assert len(broker.sent) == 1
    assert loop._position_quantity(SYMBOL) == 0  # Acceptance is not a fill.
    assert not loop.evaluate(now=SESSION + timedelta(seconds=1))
    report = broker.reports[0]
    report.filled_quantity = report.quantity
    report.avg_fill_price = 1000.0
    report.status = OrderStatus.FILLED
    loop.poll_resting_orders(now=SESSION)
    assert loop._position_quantity(SYMBOL) == report.quantity
    assert (
        runner.ledger.portfolio("u1", deployment_id=loop.deployment_id, mode="PAPER")
        .position(SYMBOL)
        .quantity
        == 0
    )
    monkeypatch.setattr(loop, "live_frame", lambda symbol: None)
    assert loop.evaluate(now=datetime(2026, 9, 14, 15, 0))
    assert len(broker.sent) == 2 and broker.sent[-1].side.name == "SELL"
    assert broker.sent[-1].broker_params["product"] == "INTRADAY"
    assert not loop.evaluate(now=datetime(2026, 9, 14, 15, 1))  # Pending exit cannot duplicate.
    broker.reports[-1].filled_quantity = broker.reports[-1].quantity
    broker.reports[-1].avg_fill_price = 1000.0
    broker.reports[-1].status = OrderStatus.FILLED
    loop.poll_resting_orders(now=datetime(2026, 9, 14, 15, 1))
    assert loop._position_quantity(SYMBOL) == 0
    assert not loop.evaluate(now=datetime(2026, 9, 14, 15, 2))


def test_live_partial_fill_cancel_race_requires_broker_confirmation(
    wired, deployment, app_db, monkeypatch
):
    _, loop, broker = live_loop(wired, deployment, app_db, monkeypatch)
    loop.evaluate(now=SESSION)
    report = broker.reports[0]
    report.filled_quantity = 2
    report.avg_fill_price = 1000.0
    report.status = OrderStatus.PARTIALLY_FILLED
    cutoff = datetime(2026, 9, 14, 14, 30)
    loop.poll_resting_orders(now=cutoff)
    order = loop.orders.open_orders("u1")[0]
    assert order["status"] == "CANCEL_PENDING"
    assert loop._position_quantity(SYMBOL) == 2
    report.filled_quantity = 4
    report.avg_fill_price = 1050.0  # Second two shares cost 1100 each.
    report.status = OrderStatus.CANCELLED
    loop.poll_resting_orders(now=cutoff)
    position = loop._portfolio().position(SYMBOL)
    assert position.quantity == 4 and position.avg_price == pytest.approx(1050.0)
    assert loop.orders.get(order["order_id"], "u1")["status"] == "CANCELLED"
    loop.poll_resting_orders(now=cutoff)
    assert loop._position_quantity(SYMBOL) == 4  # Repeated snapshot / restart safe.


def test_live_unknown_report_fails_closed_and_mode_gate_blocks_transmission(
    wired, deployment, app_db, monkeypatch
):
    _, loop, broker = live_loop(wired, deployment, app_db, monkeypatch)
    RiskStateService(SqlRiskStateStorage(app_db), get_settings()).set_execution_mode("paper", reason="test", actor="u1")
    loop.evaluate(now=SESSION)
    assert not broker.sent
    RiskStateService(SqlRiskStateStorage(app_db), get_settings()).set_execution_mode("live", reason="test", actor="u1")
    loop.acted.clear()
    loop.evaluate(now=SESSION + timedelta(days=1))
    assert len(broker.sent) == 1
    broker.reports.clear()
    with pytest.raises(RuntimeError, match="outcome unknown"):
        loop.poll_resting_orders(now=SESSION + timedelta(days=1))


def test_delayed_candle_publication_retries_without_trading_stale_data(wired, deployment):
    runner, _ = wired()
    loop = _loop(runner, deployment)
    loop.config.timeframe = "5m"
    calls = []
    loop.intraday_loaders[SYMBOL] = lambda: (
        calls.append(1) or candles(n=8 if len(calls) == 1 else 10)
    )
    _, behind = loop._completed_intraday_frame(SYMBOL, candles(), SESSION)
    assert behind == 1
    _, behind = loop._completed_intraday_frame(SYMBOL, candles(), SESSION + timedelta(seconds=1))
    assert behind == 1 and len(calls) == 1
    _, behind = loop._completed_intraday_frame(SYMBOL, candles(), SESSION + timedelta(seconds=5))
    assert behind == 0 and len(calls) == 2


def test_scheduler_runs_live_deployments_and_restart_keeps_pending_order_guard(
    wired, deployment, app_db, monkeypatch
):
    from forward.appdb.execution_schema import deployments

    runner, loop, broker = live_loop(wired, deployment, app_db, monkeypatch)
    with app_db.session() as session:
        session.execute(
            deployments.update()
            .where(deployments.c.deployment_id == loop.deployment_id)
            .values(mode="LIVE")
        )
    runner.pass_once(now=SESSION)
    assert len(broker.sent) == 1
    # A new scheduler has no in-memory acted set. The durable pending order still guards the intent.
    restarted, _ = wired()
    restarted._broker_provider = lambda: broker
    restarted.pass_once(now=SESSION + timedelta(seconds=5))
    assert len(broker.sent) == 1
    assert restarted._loops[loop.deployment_id].row["mode"] == "LIVE"


def test_broker_sync_failure_blocks_scheduler_evaluation(wired, deployment, app_db, monkeypatch):
    from forward.appdb.execution_schema import deployments

    runner, loop, broker = live_loop(wired, deployment, app_db, monkeypatch)
    with app_db.session() as session:
        session.execute(
            deployments.update()
            .where(deployments.c.deployment_id == loop.deployment_id)
            .values(mode="LIVE")
        )
    runner.pass_once(now=SESSION)
    broker.reports.clear()
    runner.pass_once(now=SESSION + timedelta(seconds=5))
    assert len(broker.sent) == 1
    assert loop.state == "ERROR"
    assert "synchronisation unavailable" in loop.skipped_reason


def test_pending_live_entries_reserve_position_slots(wired, deployment, app_db, monkeypatch):
    _, loop, broker = live_loop(wired, deployment, app_db, monkeypatch)
    loop.config.symbols = (SYMBOL, "TCS")
    loop.config.max_open_positions = 1
    loop.venue.prices.prices["TCS"] = 1000.0
    loop.frames["TCS"] = loop.frames[SYMBOL].copy()
    loop.evaluate(now=SESSION)
    assert len(broker.sent) == 1
    assert loop._open_position_count() == 1


def test_partial_entry_stop_cancels_remainder_before_closing(
    wired, deployment, app_db, monkeypatch
):
    _, loop, broker = live_loop(wired, deployment, app_db, monkeypatch)
    loop.evaluate(now=SESSION)
    report = broker.reports[0]
    report.filled_quantity = 2
    report.avg_fill_price = 1000.0
    report.status = OrderStatus.PARTIALLY_FILLED
    loop.poll_resting_orders(now=SESSION)
    loop.venue.prices.prices[SYMBOL] = 890.0
    loop.evaluate(now=SESSION + timedelta(seconds=1))
    assert broker.cancelled == [report.broker_order_id]
    assert len(broker.sent) == 1  # Cannot sell while more entry shares may fill.
    report.status = OrderStatus.CANCELLED
    loop.poll_resting_orders(now=SESSION + timedelta(seconds=2))
    loop.evaluate(now=SESSION + timedelta(seconds=2))
    assert len(broker.sent) == 2
    assert broker.sent[-1].side.name == "SELL" and broker.sent[-1].quantity == 2


def test_live_and_paper_deployments_have_distinct_order_identity(wired, deployment):
    from forward.execution.oms import OrderDraft

    runner, _ = wired()
    loop = _loop(runner, deployment)
    draft = OrderDraft(
        user_id="u1", symbol=SYMBOL, side="BUY", quantity=1, strategy_id="same", strategy_version=1
    )
    first = loop._idempotency_key(draft, SYMBOL, "BUY")
    loop.row = {**loop.row, "deployment_id": "other-deployment", "mode": "LIVE"}
    assert loop._idempotency_key(draft, SYMBOL, "BUY") != first


def test_protective_exit_works_with_missing_completed_intraday_candles(
    wired, deployment, monkeypatch
):
    runner, feed = wired()
    loop = _loop(runner, deployment)
    loop.bind()
    loop.evaluate(now=SESSION)
    loop.config.timeframe = "5m"
    loop.config.signal_basis = "closed_bar"
    loop.intraday_loaders[SYMBOL] = lambda: pd.DataFrame()
    loop.frames.clear()
    feed.prices[SYMBOL] = 890.0
    assert loop.evaluate(now=SESSION + timedelta(seconds=1))
    assert loop._position_quantity(SYMBOL) == 0
