from types import SimpleNamespace

import pandas as pd
import pytest

from forward.backtest.config import BacktestRunConfig
from forward.backtest.readiness import check_readiness
from forward.signals.conflicts import contradictory_formula
from forward.strategy.definition import validate_definition


@pytest.mark.parametrize("source", [
    "rsi(14) < 30 and rsi(14) > 70",
    "price >= 100 and price < 100",
    "30 > rsi(14) > 70",
    "(price < 10 or price > 100) and price == 50",
    "price == 50 and price != 50",
])
def test_impossible_conditions(source):
    assert contradictory_formula(source)
    report = validate_definition({"engine_key":"signals_entry", "rules":{"entry":{"setup":"custom_entry","custom_entry_formula":source},"exit":{}}})
    assert any(issue["code"] == "contradictory_conditions" for issue in report["errors"])


@pytest.mark.parametrize("source", [
    "rsi(14) < 30 or rsi(14) > 70",
    "price >= 100 and price <= 100",
    "rsi(5) < 30 and rsi(14) > 70",
    "(price < 10 and price > 20) or price == 50",
    "price > sma(20) * 1.05",
    "False",  # Intentional signal-exit disabling remains supported.
])
def test_feasible_or_unproven_conditions_are_not_rejected(source):
    assert not contradictory_formula(source)


def runner(frames, formula="price > 0", start="2026-01-20", end="2026-02-28", history=10):
    return SimpleNamespace(
        config=BacktestRunConfig(strategy="signals_entry",engine_key="signals_entry",symbols=["OK","MISSING"],start=start,end=end),
        _version_definition={"engine_key":"signals_entry","rules":{"entry":{"setup":"custom_entry","custom_entry_formula":formula,"min_history_bars":history},"exit":{"min_history_bars":history}}},
        resolve_symbols=lambda: ["OK","MISSING"], _warmup_need=lambda key: 0,
        load_market_frames=lambda symbols: (frames, []),
    )


def frame():
    dates = pd.date_range("2026-01-01", periods=45)
    return pd.DataFrame({"open":100.,"high":102.,"low":99.,"close":101.,"volume":1000.},index=dates)


def test_readiness_checks_each_requested_stock():
    result = check_readiness(runner({"OK":frame()}))
    assert not result["ok"]
    assert result["stocks"][0]["ready"]
    assert result["issues"] == [{"symbol":"MISSING","message":"No cached candles for this timeframe."}]


def test_readiness_allows_complete_history_and_rejects_invalid_prices():
    result = check_readiness(runner({"OK":frame(),"MISSING":frame()}))
    assert result["ok"]
    bad = frame()
    bad.iloc[-1, bad.columns.get_loc("close")] = float('nan')
    result = check_readiness(runner({"OK":bad,"MISSING":frame()}))
    assert not result["ok"]
    assert "invalid prices" in result["issues"][0]["message"]


def test_readiness_history_and_selected_dates():
    result = check_readiness(runner({"OK":frame()},history=50))
    assert "50 history bars" in result["issues"][0]["message"]
    result = check_readiness(runner({"OK":frame()},start="2027-01-01",end="2027-02-01"))
    assert "selected dates" in result["issues"][0]["message"]


def test_readiness_requires_volume_only_for_volume_rules():
    data = frame().drop(columns="volume")
    assert check_readiness(runner({"OK":data}))["stocks"][0]["ready"]
    result = check_readiness(runner({"OK":data},formula="mfi(14) < 30"))
    assert "volume data" in result["issues"][0]["message"]
    assert not result["stocks"][0]["ready"]


def test_http_readiness_does_not_create_run(auth_client, monkeypatch):
    from forward.backtest.runner import BacktestRunner
    monkeypatch.setattr(
        BacktestRunner,
        "load_market_frames",
        lambda self, symbols: ({"OK": frame()}, []),
    )
    before = auth_client.get("/api/v1/backtests").json()
    response = auth_client.post("/api/v1/backtests/readiness",json={"strategy":"sma_crossover","symbols":["OK","MISSING"],"warmup_bars":10})
    assert response.status_code == 200, response.text
    assert not response.json()["ok"]
    assert auth_client.get("/api/v1/backtests").json() == before
