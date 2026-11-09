from unittest.mock import Mock

import pytest

from forward.backtest.config import BacktestRunConfig
from forward.backtest.runner import BacktestRunner
from forward.core.backtest_ports import BacktestMarketDataPort
from forward.strategy.definition import resolve_rules, validate_definition
from forward.strategy.python_import import PythonStrategyError, compile_python_strategy
from forward.strategy.strategies.python_rules import PythonRules

SOURCE = """def strategy(ctx):
    fast = ctx.ema(9)
    slow = ctx.ema(21)
    return {"entry": fast > slow, "exit": ctx.rsi(14) > 70}
"""


def definition(source=SOURCE):
    return {"engine_key":"python_rules", "code":{"language":"python", "interface_version":1, "source":source}, "context":{"style":"swing", "timeframe":"1d", "hold_overnight":True}}


def test_compile_and_validate_and_resolve_same_logic():
    rules = compile_python_strategy(SOURCE)
    assert rules["entry"]["custom_entry_formula"] == "ema(9) > ema(21)"
    assert rules["entry"]["min_history_bars"] == 21
    report = validate_definition(definition())
    assert report["ok"], report
    assert "no_exit_rule" not in {warning["code"] for warning in report["warnings"]}
    resolved = resolve_rules(definition())
    assert resolved.ok
    assert resolved.entry.custom_entry_formula == rules["entry"]["custom_entry_formula"]
    runner = BacktestRunner(
        BacktestRunConfig(strategy_id="test", strategy_version=1),
        version_definition=definition(),
        market_data=Mock(spec=BacktestMarketDataPort),
        max_persisted_trades=20_000,
        max_curve_points=20_000,
    )
    strategy = runner.build_strategy()
    assert isinstance(strategy.inner, PythonRules)
    assert strategy.inner.entries.custom_entry_formula == resolved.entry.custom_entry_formula


@pytest.mark.parametrize("source", [
    "import os\ndef strategy(ctx):\n    return {}",
    "def strategy(ctx):\n    while True: pass",
    'def strategy(ctx):\n    return {"entry": ctx.__class__, "exit": False}',
    'def strategy(ctx):\n    return {"entry": open(1), "exit": False}',
    'def strategy(ctx):\n    return {"entry": ctx.typo > 0, "exit": False}',
    'def strategy(ctx):\n    return {"entry": ctx.rsi(0), "exit": False}',
])
def test_invalid_or_unsupported_code_never_executes(source):
    with pytest.raises(PythonStrategyError):
        compile_python_strategy(source)
    assert not validate_definition(definition(source))["ok"]


def test_alias_assignment_keeps_previous_value():
    rules = compile_python_strategy('def strategy(ctx):\n    fast = ctx.ema(9)\n    fast = fast + 1\n    return {"entry": fast > ctx.price, "exit": False}')
    assert rules["entry"]["custom_entry_formula"] == 'ema(9) + 1 > price'


def test_session_requirements_apply_to_imports():
    report = validate_definition(definition('def strategy(ctx):\n    return {"entry": ctx.price > ctx.session_vwap, "exit": False}'))
    assert any(e["code"] == "intraday_indicator_on_daily" for e in report["errors"])


def test_imported_strategy_trades_through_the_engine():
    from datetime import datetime, timedelta

    from forward.backtest.engine import BacktestEngine
    from forward.core.feeds import ListFeed
    from forward.core.instrument_models import Instrument
    from forward.core.market_models import Bar, MarketSnapshot
    source = 'def strategy(ctx):\n    return {"entry": ctx.price > 0, "exit": ctx.pnl_pct > 1}'
    strategy = PythonRules(source=source)
    inst = Instrument(symbol="TEST")
    snapshots = []
    for index, price in enumerate([100, 100, 100, 100, 110, 110]):
        ts = datetime(2026, 1, 5) + timedelta(days=index)
        snapshots.append(MarketSnapshot(ts=ts, bars={"TEST": Bar(ts=ts,open=price,high=price,low=price,close=price,volume=10000)}))
    result = BacktestEngine(ListFeed(snapshots, {"TEST":inst}), strategy).run()
    assert len(result.trades) == 1
    assert result.trades.iloc[0].entry_price < result.trades.iloc[0].exit_price


def test_http_save_and_reopen_preserve_source(auth_client):
    created = auth_client.post("/api/v1/strategies", json={"name":"Imported Python QA", "kind":"code", "engine_key":"python_rules"})
    assert created.status_code == 201, created.text
    strategy_id = created.json()["strategy_id"]
    saved = auth_client.post(f"/api/v1/strategies/{strategy_id}/versions", json={"definition":definition(), "change_note":"Python import"})
    assert saved.status_code == 201, saved.text
    version = saved.json()["version"]
    reopened = auth_client.get(f"/api/v1/strategies/{strategy_id}/versions/{version}")
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["definition"]["code"]["source"] == SOURCE
    assert resolve_rules(reopened.json()["definition"]).ok
