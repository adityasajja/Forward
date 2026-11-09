import pandas as pd
import pytest

from forward.backtest.sector_opening_replay import replay_path, wilson


def path():
    return pd.DataFrame({"ts": pd.date_range("2026-09-01 09:20", "2026-09-01 15:20", freq="min"),
                         "open": 100., "high": 100., "low": 100., "close": 100.})


@pytest.mark.parametrize("side,field,price", [("BUY", "high", 102), ("SELL", "low", 98)])
def test_actual_fill_target_and_costs(side, field, price):
    p = path()
    p.loc[1, field] = price
    r = replay_path(p, side)
    assert r["status"] == "resolved" and r["reason"] == "target"
    assert r["fees"] > 0 and r["net_pnl"] < r["gross_pnl"]
    assert r["quantity"] == 4900
    assert r["target"] == r["entry"] * (1.01 if side == "BUY" else .99)


def test_ambiguity_is_conservative_and_sensitivity_explicit():
    p = path()
    p.loc[0, ["high", "low"]] = [102, 98]
    pessimistic = replay_path(p, "BUY", slippage_bps=0)
    optimistic = replay_path(p, "BUY", slippage_bps=0, target_first=True)
    assert pessimistic["reason"] == "stop_both_hit"
    assert optimistic["reason"] == "target_both_hit"
    assert pessimistic["net_pnl"] < 0 < optimistic["net_pnl"]


def test_gap_stop_fills_at_worse_open():
    p = path()
    p.loc[1, ["open", "high", "low", "close"]] = [97, 97, 97, 97]
    assert replay_path(p, "BUY", slippage_bps=0)["exit"] == 97


def test_missing_before_exit_unresolved_not_zero():
    r = replay_path(path().drop(index=1), "BUY")
    assert r["status"] == "unresolved" and "net_pnl" not in r


def test_missing_after_observed_exit_is_irrelevant():
    p = path()
    p.loc[0, "high"] = 102
    assert replay_path(p.iloc[:1], "BUY")["status"] == "resolved"


def test_squareoff_before_range_and_capital_cap():
    p = path()
    p.loc[len(p)-1, ["high", "low"]] = [102, 98]
    r = replay_path(p, "BUY", equity=600000, slippage_bps=0)
    assert r["reason"] == "square_off" and r["quantity"] == 4900


def test_prior_adv_caps_quantity_and_adds_impact():
    r = replay_path(path(), "BUY", adv=20000)
    assert r["quantity"] == 1000 and r["requested_quantity"] == 4900
    assert r["entry"] > 100.05 and r["impact_bps"] > 0


def test_no_late_entry_and_wilson_empty():
    assert replay_path(path().iloc[1:], "BUY")["status"] == "missing_entry"
    assert wilson(0, 0) is None
    assert wilson(5, 10)[0] < 50 < wilson(5, 10)[1]
