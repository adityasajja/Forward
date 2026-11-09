import pandas as pd
import pytest

from forward.backtest.sector_momentum import Config, backtest


def fixture(direction=1):
    day = "2026-09-01"
    universe = pd.DataFrame(
        [
            [day, "NIFTY", "index", ""],
            [day, "BANK", "sector", "bank"],
            [day, "AAA", "stock", "bank"],
            [day, "BBB", "stock", "bank"],
        ],
        columns=["date", "symbol", "kind", "sector"],
    )
    rows = []
    for symbol, change in [("NIFTY", 1), ("BANK", 2), ("AAA", 4), ("BBB", 3)]:
        price = 100 + direction * change
        rows.append([f"{day} 09:15", symbol, price, price, price, price, 100])
    for ts in pd.date_range(f"{day} 09:20", f"{day} 15:20", freq="min"):
        rows.append([str(ts), "AAA", 100, 100, 100, 100, 100])
    return pd.DataFrame(
        rows, columns=["timestamp", "symbol", "open", "high", "low", "close", "prev_close"]
    ), universe


@pytest.mark.parametrize("direction", [1, -1])
def test_target_and_selection(direction):
    b, u = fixture(direction)
    b.loc[b.timestamp.str.endswith("09:20:00"), "high" if direction > 0 else "low"] = (
        101 if direction > 0 else 99
    )
    t, _, s = backtest(b, u, Config(slippage_bps=0, fee_bps=0))
    assert t.iloc[0].symbol == "AAA"
    assert t.iloc[0].reason == "target"
    assert t.iloc[0].net_pnl == 1000
    assert s["final_equity"] == 101000


def test_ambiguous_bar_stop_first():
    b, u = fixture()
    mask = b.timestamp.str.endswith("09:20:00")
    b.loc[mask, ["high", "low"]] = [102, 98]
    t, _, _ = backtest(b, u, Config(slippage_bps=0, fee_bps=0))
    assert t.iloc[0].reason == "stop_both_hit"
    assert t.iloc[0].net_pnl == -1000


def test_missing_minutes_skips_trade():
    b, u = fixture()
    b = b[~b.timestamp.str.endswith("10:00:00")]
    t, d, _ = backtest(b, u)
    assert t.empty
    assert d.iloc[0].status == "incomplete_trade_session"


def test_missing_data_after_exit_does_not_invalidate_trade():
    b, u = fixture()
    b.loc[b.timestamp.str.endswith("09:20:00"), "high"] = 102
    b = b[~b.timestamp.str.endswith("10:00:00")]
    t, d, _ = backtest(b, u, Config(slippage_bps=0, fee_bps=0))
    assert t.iloc[0].reason == "target"
    assert d.iloc[0].status == "traded"


def test_stop_gap_and_costs():
    b, u = fixture()
    b.loc[b.timestamp.str.endswith("09:21:00"), ["open", "high", "low", "close"]] = 97
    t, _, _ = backtest(b, u, Config(slippage_bps=0, fee_bps=5))
    assert t.iloc[0].exit == 97
    assert t.iloc[0].reason == "stop_gap"
    assert t.iloc[0].net_pnl < t.iloc[0].gross_pnl


def test_selection_ignores_opening_candle_close_and_later_winner():
    b, u = fixture()
    b.loc[b.symbol == "BBB", ["high", "close"]] = 150
    t, _, _ = backtest(b, u)
    assert t.iloc[0].symbol == "AAA"
    assert t.iloc[0].reason == "square_off"
