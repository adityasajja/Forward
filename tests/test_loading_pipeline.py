from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

from forward.api.routers import paper
from forward.appdb.order_event_repository import OrderEventRepository
from forward.appdb.order_repository import OrderRepository
from forward.appdb.paper_ledger_storage import SqlPaperLedgerStorage
from forward.appdb.portfolio_storage import SqlPortfolioStorage
from tests.paper_price_fakes import no_live_price


def test_fill_queries_are_batched():
    session = Mock()
    session.execute.return_value.all.return_value = []
    OrderEventRepository.fills_for_orders(session, [str(i) for i in range(1200)])
    assert session.execute.call_count == 3
    session.reset_mock()
    assert OrderEventRepository.fills_for_orders(session, []) == {}
    session.execute.assert_not_called()


def test_batched_fill_replay_keeps_incremental_quantities(monkeypatch):
    order = {"order_id": "o", "deployment_id": "d", "symbol": "ABC", "side": "BUY"}
    monkeypatch.setattr(OrderRepository, "for_replay", lambda *a, **k: [order])
    events = [{"filled_qty": qty, "filled_price": 10, "ts": "2026-10-12"} for qty in [2, 2, 5]]
    batch = Mock(return_value={"o": events})
    monkeypatch.setattr(OrderEventRepository, "fills_for_orders", batch)
    rows = SqlPortfolioStorage(MagicMock()).fills("u")
    assert [r["qty"] for r in rows] == [2, 3]
    assert all(r["deployment_id"] == "d" for r in rows)
    assert batch.call_args.args[1] == ["o"]


def test_initial_paper_payload_is_owner_scoped(monkeypatch):
    service = Mock()
    service.list.return_value = [{"deployment_id": "d"}]
    monkeypatch.setattr(paper, "_service", lambda: service)
    monitor = Mock()
    monitor.overview.return_value = {"status": {"deployment_id": "d"}}
    monkeypatch.setattr("forward.services.monitoring.DeploymentMonitor", lambda: monitor)
    result = paper.list_deployments(SimpleNamespace(user_id="owner"), include_overview=True)
    assert result["overview"] == monitor.overview.return_value
    monitor.overview.assert_called_once_with("owner", "d")
    monitor.reset_mock()
    assert "overview" not in paper.list_deployments(SimpleNamespace(user_id="owner"))
    monitor.overview.assert_not_called()


def test_overview_failure_preserves_deployment_list(monkeypatch):
    service = Mock()
    service.list.return_value = [{"deployment_id": "d"}]
    monkeypatch.setattr(paper, "_service", lambda: service)
    monitor = Mock()
    monitor.overview.side_effect = RuntimeError("unavailable")
    monkeypatch.setattr("forward.services.monitoring.DeploymentMonitor", lambda: monitor)
    result = paper.list_deployments(SimpleNamespace(user_id="owner"), include_overview=True)
    assert result["total"] == 1
    assert "overview" not in result


def test_navigation_summary_reads_metadata_only(monkeypatch):
    from contextlib import nullcontext

    from forward.appdb.deployment_repository import DeploymentRepository

    session = object()
    db = SimpleNamespace(session=lambda: nullcontext(session))
    monkeypatch.setattr("forward.appdb.engine.get_app_db", lambda: db)
    read = Mock(return_value=[{"status": "RUNNING"}, {"status": "PAUSED"}])
    monkeypatch.setattr(DeploymentRepository, "list_for_user", read)
    assert paper.deployment_summary(SimpleNamespace(user_id="owner")) == {
        "total": 2,
        "running_count": 1,
    }
    read.assert_called_once_with(session, "owner")


def test_paper_ledger_batch_preserves_state_event_quantities(monkeypatch):
    from contextlib import nullcontext

    from forward.appdb import paper_ledger_storage as storage_module

    order = {"order_id": "o"}
    monkeypatch.setattr(OrderRepository, "for_replay", lambda *a, **k: [order])
    events = [
        {"filled_qty": 2, "to_status": "PARTIALLY_FILLED"},
        {"filled_qty": 2, "to_status": "PARTIALLY_FILLED"},
        {"filled_qty": 3, "to_status": "CANCELLED"},
        {"filled_qty": 5, "to_status": "FILLED"},
    ]
    batch = Mock(return_value={"o": events})
    monkeypatch.setattr(OrderEventRepository, "fills_for_orders", batch)
    monkeypatch.setattr(
        storage_module,
        "_fill_from_event",
        lambda order_row, event, quantity: SimpleNamespace(ts=0, qty=quantity),
    )
    db = SimpleNamespace(session=lambda: nullcontext(Mock()))
    fills = storage_module.SqlPaperLedgerStorage(db).fills("owner", deployment_id="d", mode=None)
    assert [f.qty for f in fills] == [2, 2]
    assert batch.call_args.kwargs == {"include_state_changes": True}


def test_data_status_reuses_unchanged_file_and_invalidates(tmp_path, monkeypatch):
    import pandas as pd

    from forward.data import data_status

    path = tmp_path / "bars.parquet"
    path.write_bytes(b"one")
    read = Mock(return_value=pd.DataFrame({"ts": ["2026-10-09"]}))
    monkeypatch.setattr(data_status.pd, "read_parquet", read)
    data_status._last_day_version.cache_clear()
    assert str(data_status._last_day(path)) == "2026-10-09"
    data_status._last_day(path)
    assert read.call_count == 1
    path.write_bytes(b"changed-size")
    data_status._last_day(path)
    assert read.call_count == 2


def test_deployment_batch_matches_individual_accounting(monkeypatch):
    from datetime import UTC, datetime

    from forward.core.enums import Side
    from forward.core.instrument_models import Instrument
    from forward.core.order_models import Fill
    from forward.services.paper_ledger import PaperLedger

    storage = Mock()
    ledger = PaperLedger(storage, price_source=no_live_price)
    deployments = [
        {"deployment_id": "a", "capital": 10000},
        {"deployment_id": "b", "capital": 20000},
    ]
    orders = {
        "buy": {"deployment_id": "a"},
        "sell": {"deployment_id": "a"},
        "short": {"deployment_id": "b"},
    }
    instrument = Instrument(symbol="ABC")
    fills = [
        Fill(
            order_id=key,
            instrument=instrument,
            side=side,
            quantity=qty,
            price=price,
            ts=datetime(2026, 10, 12, tzinfo=UTC),
            commission=2,
        )
        for key, side, qty, price in [
            ("buy", Side.BUY, 10, 100),
            ("sell", Side.SELL, 4, 110),
            ("short", Side.SELL, 3, 105),
        ]
    ]
    storage.fills_by_deployment.return_value = {
        "a": fills[:2],
        "b": fills[2:],
    }
    storage.fills.side_effect = lambda _owner, *, deployment_id, **_kwargs: (
        storage.fills_by_deployment.return_value[deployment_id]
    )
    monkeypatch.setattr(ledger, "_marks", lambda p, **kwargs: {"ABC": 108})
    result = ledger.snapshots_for_deployments("owner", deployments)
    storage.fills_by_deployment.assert_called_once_with("owner", ["a", "b"])
    for row in deployments:
        key = row["deployment_id"]
        monkeypatch.setattr(
            ledger,
            "fills",
            lambda *args, **kwargs: [
                fill
                for fill in fills
                if orders[fill.order_id]["deployment_id"] == kwargs["deployment_id"]
            ],
        )
        assert result[key] == ledger.snapshot(
            "owner", deployment_id=key, initial_cash=row["capital"]
        )
    monkeypatch.setattr(ledger, "_marks", lambda p, **kwargs: {})
    result = ledger.snapshots_for_deployments("owner", deployments)
    assert all(not r["complete"] and r["unpriced_symbols"] == ["ABC"] for r in result.values())


def test_replay_reads_all_orders_and_respects_owner_and_mode(app_db):
    from datetime import datetime, timedelta

    from sqlalchemy import event, insert

    from forward.appdb.execution_schema import order_events, orders
    from forward.appdb.identity_repository import UserRepository
    from forward.services.paper_ledger import PaperLedger

    now = datetime(2026, 10, 12)
    with app_db.session() as session:
        for uid in ["replay-owner", "other-owner"]:
            user = UserRepository.create(
                session, email=f"{uid}@example.com", username=uid, password_hash="x", role="trader"
            )
            if uid == "replay-owner":
                owner = user["user_id"]
            else:
                other = user["user_id"]
        rows = [
            dict(
                order_id=f"o{i}",
                user_id=owner,
                symbol="ABC",
                side="BUY",
                quantity=1,
                status="FILLED",
                mode="PAPER",
                created_at=now + timedelta(seconds=i),
                updated_at=now,
            )
            for i in range(1201)
        ]
        rows += [
            dict(rows[0], order_id="other", user_id=other),
            dict(rows[0], order_id="live", mode="LIVE"),
        ]
        session.execute(insert(orders), rows)
        session.execute(
            insert(order_events),
            [
                dict(
                    order_event_id=f"e{i}",
                    order_id=f"o{i}",
                    seq=1,
                    to_status="FILLED",
                    ts=now,
                    filled_qty=1,
                    filled_price=100,
                )
                for i in range(1201)
            ],
        )
        session.commit()
    queries = []

    def observed(*args):
        queries.append(args[2])

    event.listen(app_db.engine, "before_cursor_execute", observed)
    try:
        ledger = PaperLedger(storage=SqlPaperLedgerStorage(app_db), price_source=no_live_price)
        fills = ledger.fills(owner, mode="PAPER")
        assert len(fills) == 1201
        assert sum(query.lstrip().upper().startswith("SELECT") for query in queries) == 4
        assert not any("count(" in query.lower() for query in queries)
        with app_db.session() as session:
            assert len(OrderRepository.for_replay(session, owner, mode="LIVE")) == 1
            assert OrderRepository.for_replay(session, owner, deployment_id="missing") == []
    finally:
        event.remove(app_db.engine, "before_cursor_execute", observed)


def test_grouped_prices_resolve_each_instrument_once_and_refresh():
    from datetime import UTC, datetime

    from forward.core.enums import Side
    from forward.core.instrument_models import Instrument
    from forward.core.order_models import Fill
    from forward.core.paper_models import TickPrice
    from forward.services import paper_ledger as paper

    storage = Mock()
    live = Mock(return_value=None)
    ledger = paper.PaperLedger(storage, price_source=live)
    rows = [{"deployment_id": key, "capital": 1000} for key in ["a", "b", "c"]]
    fills = [
        Fill(
            order_id=key,
            instrument=Instrument(symbol="ABC", exchange=exchange),
            side=Side.BUY,
            quantity=1,
            price=100,
            ts=datetime(2026, 10, 12, tzinfo=UTC),
        )
        for key, exchange in [("a", "NSEEQ"), ("b", "NSEEQ"), ("c", "BSE")]
    ]
    storage.fills_by_deployment.return_value = {
        "a": fills[:1],
        "b": fills[1:2],
        "c": fills[2:],
    }
    result = ledger.snapshots_for_deployments("owner", rows)
    assert live.call_count == 2
    assert not result["a"]["complete"] and not result["b"]["complete"]
    live.return_value = TickPrice(price=120)
    result = ledger.snapshots_for_deployments("owner", rows)
    assert live.call_count == 4
    assert result["a"]["equity"] == 1020
    live.return_value = None
    result = ledger.snapshots_for_deployments("owner", rows)
    assert all(not value["complete"] for value in result.values())
    assert live.call_count == 6


def test_health_reads_risk_once_for_consistent_flags(monkeypatch):
    from forward.api.legacy import system

    risk = Mock(return_value={"execution_mode": "live", "kill_switch": True})
    monkeypatch.setattr(system, "_risk_state", risk)
    monkeypatch.setattr(system, "_db_health", lambda: True)
    monkeypatch.setattr(
        system, "get_settings", lambda: SimpleNamespace(env="dev", iifl_session_cache="missing")
    )
    monkeypatch.setattr("forward.brokers.iifl.auth.SessionStore.load", lambda self: None)
    result = system.health()
    assert result.execution_mode == "live" and result.kill_switch is True
    risk.assert_called_once_with()
