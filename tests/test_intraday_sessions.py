from datetime import datetime

import pandas as pd
import pytest

from forward.backtest.config import BacktestRunConfig, ConfigError
from forward.backtest.engine import BacktestConfig, BacktestEngine
from forward.core.enums import OrderStatus
from forward.core.feeds import ListFeed
from forward.core.instrument_models import Instrument
from forward.core.market_models import Bar, MarketSnapshot
from forward.data.intraday_history import load_intraday
from forward.strategy.base import Strategy


class AlwaysBuy(Strategy):
    def on_bar(self, ctx):
        if not ctx.has_position("TEST"):
            ctx.order("TEST", 10)


def engine(times):
    inst = Instrument(symbol="TEST")
    snaps = [MarketSnapshot(ts=datetime.fromisoformat(t), bars={"TEST": Bar(ts=datetime.fromisoformat(t), open=100, high=102, low=99, close=101, volume=10000)}) for t in times]
    return BacktestEngine(ListFeed(snaps, {"TEST": inst}), AlwaysBuy(), BacktestConfig(square_off_eod=True))


def test_square_off_fills_same_session_at_scheduled_open():
    run = engine(["2026-09-17T09:15", "2026-09-17T09:20", "2026-09-17T14:45", "2026-09-17T15:15", "2026-09-18T09:15", "2026-09-18T09:20", "2026-09-18T15:15"])
    result = run.run()
    assert len(result.trades) == 2
    assert all(pd.Timestamp(row.entry_ts).date() == pd.Timestamp(row.exit_ts).date() for row in result.trades.itertuples())
    assert all(pd.Timestamp(t).strftime("%H:%M") == "15:15" for t in result.trades.exit_ts)
    assert run.portfolio.position("TEST").is_flat


def test_cutoff_cancels_queued_entry_before_filling():
    run = engine(["2026-09-17T14:40", "2026-09-17T14:45", "2026-09-17T15:15"])
    assert run.run().trades.empty
    assert run.broker.submitted[0].status is OrderStatus.CANCELLED


def test_missing_square_off_data_fails_instead_of_overnight_fill():
    with pytest.raises(ValueError, match="square-off"):
        engine(["2026-09-17T09:15", "2026-09-17T09:20", "2026-09-18T09:15"]).run()


def test_intraday_requires_square_off_and_ordered_clocks():
    with pytest.raises(ConfigError, match="same-day"):
        BacktestRunConfig(timeframe="5m", symbols=["TEST"]).validate()
    with pytest.raises(ConfigError, match="entry cutoff"):
        BacktestRunConfig(timeframe="5m", symbols=["TEST"], square_off_eod=True, entry_cutoff="15:20").validate()


def test_resampling_uses_complete_session_buckets(tmp_path):
    folder = tmp_path / "iifl_1m"
    folder.mkdir()
    dates = pd.date_range("2026-09-17 09:15", periods=7, freq="min")
    pd.DataFrame({"ts": dates, "open": range(100,107), "high": range(101,108), "low": range(99,106), "close": range(100,107), "volume": 10}).to_parquet(folder / "TEST.parquet")
    bars = load_intraday(tmp_path, ["TEST"], "NSEEQ", "5m")["TEST"]
    assert len(bars) == 1
    assert bars.iloc[0]["close"] == 104
    assert bars.iloc[0]["volume"] == 50
    assert bars.index[0].strftime("%H:%M") == "09:15"


def test_conflicting_history_is_not_silently_resolved(tmp_path):
    folder = tmp_path / "iifl_1m"
    folder.mkdir()
    pd.DataFrame({"ts": [pd.Timestamp("2026-09-17 09:15")] * 2, "open": [100, 101], "high": [102, 102], "low": [99, 99], "close": [100, 101], "volume": [10, 10]}).to_parquet(folder / "TEST.parquet")
    with pytest.raises(ValueError, match="duplicate"):
        load_intraday(tmp_path, ["TEST"], "NSEEQ", "5m")


def test_session_clocks_align_to_bar_boundaries():
    with pytest.raises(ConfigError, match="align"):
        BacktestRunConfig(timeframe="5m", symbols=["TEST"], square_off_eod=True, square_off_time="15:17").validate()
