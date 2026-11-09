from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from forward.strategy.mean_reversion import plan, signal


def sample():
    times = pd.date_range("2026-10-15 09:15", periods=21, freq="5min", tz="Asia/Kolkata")
    close = np.r_[100+.1*np.sin(np.arange(19)), 97.8, 98.0]
    bars = pd.DataFrame({"open": close-.1, "high": close+.4,
                         "low": close-.4, "close": close, "volume": 10000}, index=times)
    index = pd.Series(22000., index=times+pd.Timedelta(minutes=5))
    return bars, index, times[-1]+pd.Timedelta(minutes=5)


def test_candidate_and_causal_features():
    bars, index, now = sample()
    result = signal(bars, index, now=now)
    assert result["status"] == "candidate"
    assert result["signal_time"] == now.isoformat()
    assert result["z"] <= -2
    future = bars.iloc[[-1]].copy()
    future.index += pd.Timedelta(minutes=5)
    future.loc[:, "close"] = 1_000_000
    assert signal(pd.concat([bars, future]), index, now=now) == result
    assert signal(bars, index, now=now-pd.Timedelta(seconds=1))["reason"] == "warmup"


def test_missing_bar_not_interpolated():
    bars, index, now = sample()
    extra = bars.iloc[[-1]].copy()
    extra.index += pd.Timedelta(minutes=5)
    bars = pd.concat([bars.drop(bars.index[5]), extra])
    assert signal(bars, index, now=now+pd.Timedelta(minutes=5))["reason"] == "missing_or_duplicate_bar"


def test_trending_or_missing_index_skips():
    bars, index, now = sample()
    missing = index.drop(index.index[-2])
    assert signal(bars, missing, now=now)["reason"] == "missing_index_bar"
    index.iloc[-1] *= .99
    assert signal(bars, index, now=now)["status"] == "skip"


def test_invalid_ohlc_skips():
    bars, index, now = sample()
    bars.iloc[-1, bars.columns.get_loc("high")] = 1
    assert signal(bars, index, now=now)["reason"] == "invalid_bar"


def inputs():
    _, _, now = sample()
    return {"candidate": {"status": "candidate", "signal_time": now.isoformat(), "target": 102., "atr": 1.},
            "symbol": "EXAMPLE-EQ", "now": now+pd.Timedelta(seconds=1),
            "exchange_time": now+pd.Timedelta(milliseconds=500),
            "received_time": now+pd.Timedelta(milliseconds=800),
            "bid": 99.99, "ask": 100., "equity": 500000., "available_cash": 500000.,
            "adv20": 1_000_000., "average_value20": 100_000_000.}


def test_plan_respects_costs_and_caps():
    args = inputs()
    result = plan(**args)
    assert result["status"] == "planned"
    assert result["quantity"]*result["expected_entry"] <= 125000
    assert result["planned_net_loss"] <= 1250
    assert result["net_reward_risk"] >= 1.25
    assert result["stop"] == pytest.approx(result["expected_entry"]-1)
    assert result["expected_entry"] > args["ask"]
    assert result["target"] == 102


@pytest.mark.parametrize("field,value,reason", [
    ("bid", 99., "wide_spread"), ("average_value20", 1., "illiquid"),
    ("available_cash", 1., "insufficient_capital"), ("adv20", float("nan"), "invalid_inputs"),
])
def test_plan_rejections(field, value, reason):
    args = inputs()
    args[field] = value
    assert plan(**args)["reason"] == reason


def test_no_same_bar_or_late_entry_and_no_stale_quotes():
    args = inputs()
    when = pd.Timestamp(args["candidate"]["signal_time"])
    for now in [when, when+pd.Timedelta(seconds=31)]:
        assert plan(**{**args, "now": now})["reason"] == "entry_window"
    assert plan(**{**args, "exchange_time": when-pd.Timedelta(seconds=5)})["reason"] == "stale_quote"
    assert plan(**{**args, "exchange_time": when})["reason"] == "quote_precedes_signal"


def test_costs_reject_small_reversion():
    args = inputs()
    args["candidate"] = deepcopy(args["candidate"])
    args["candidate"]["target"] = 100.5
    assert plan(**args)["reason"] == "insufficient_net_reward"
