"""Characterization tests for forward.data.base — pivot_to_snapshots, filter_snapshots, to_frame.

These pin down current behavior (pandas-backed) so the polars migration of this
module can be verified against them.
"""

from __future__ import annotations

from datetime import datetime

import polars as pl
import pytest

from forward.core.enums import Timeframe
from forward.core.market_models import Bar, MarketSnapshot
from forward.data.base import filter_snapshots, pivot_to_snapshots, snapshots_to_frame


def _row(ts, symbol, o, h, low, c, v=100.0):
    return {"ts": ts, "symbol": symbol, "open": o, "high": h, "low": low, "close": c, "volume": v}


def _frame(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(rows)


def test_pivot_to_snapshots_groups_by_timestamp_and_preserves_values():
    t1 = datetime(2024, 1, 1, 9, 15)
    t2 = datetime(2024, 1, 1, 9, 16)
    rows = [
        _row(t1, "AAA", 1, 2, 0.5, 1.5),
        _row(t1, "BBB", 10, 11, 9, 10.5),
        _row(t2, "AAA", 1.5, 2.5, 1, 2),
    ]
    df = _frame(rows)

    snapshots = pivot_to_snapshots(df, Timeframe.MIN_1)

    assert len(snapshots) == 2
    assert snapshots[0].ts == t1
    assert snapshots[1].ts == t2
    assert set(snapshots[0].bars) == {"AAA", "BBB"}
    bar = snapshots[0].bars["AAA"]
    assert bar.open == pytest.approx(1)
    assert bar.high == pytest.approx(2)
    assert bar.low == pytest.approx(0.5)
    assert bar.close == pytest.approx(1.5)
    assert bar.volume == pytest.approx(100.0)
    assert set(snapshots[1].bars) == {"AAA"}


def test_pivot_to_snapshots_sorted_ascending_even_if_input_unsorted():
    t1 = datetime(2024, 1, 1, 9, 15)
    t2 = datetime(2024, 1, 1, 9, 16)
    rows = [
        _row(t2, "AAA", 1.5, 2.5, 1, 2),
        _row(t1, "AAA", 1, 2, 0.5, 1.5),
    ]
    df = _frame(rows)

    snapshots = pivot_to_snapshots(df, Timeframe.MIN_1)

    assert [s.ts for s in snapshots] == [t1, t2]


def test_pivot_to_snapshots_missing_volume_remains_unknown():
    t1 = datetime(2024, 1, 1, 9, 15)
    df = pl.DataFrame(
        [{"ts": t1, "symbol": "AAA", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5}]
    )

    snapshots = pivot_to_snapshots(df, Timeframe.MIN_1)

    assert snapshots[0].bars["AAA"].volume is None


def test_pivot_to_snapshots_nan_volume_remains_unknown():
    t1 = datetime(2024, 1, 1, 9, 15)
    df = pl.DataFrame(
        [
            {
                "ts": t1,
                "symbol": "AAA",
                "open": 1.0,
                "high": 2.0,
                "low": 0.5,
                "close": 1.5,
                "volume": float("nan"),
            }
        ]
    )

    snapshots = pivot_to_snapshots(df, Timeframe.MIN_1)

    assert snapshots[0].bars["AAA"].volume is None


def test_pivot_to_snapshots_empty_frame_returns_empty_list():
    df = pl.DataFrame(
        schema={
            "ts": pl.Datetime,
            "symbol": pl.Utf8,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
        }
    )

    assert pivot_to_snapshots(df, Timeframe.MIN_1) == []


def test_pivot_to_snapshots_missing_required_column_raises():
    df = pl.DataFrame([{"ts": datetime(2024, 1, 1), "symbol": "AAA", "open": 1.0}])

    with pytest.raises(ValueError, match="missing required columns"):
        pivot_to_snapshots(df, Timeframe.MIN_1)


def test_filter_snapshots_by_start_and_end():
    snaps = [
        MarketSnapshot(ts=datetime(2024, 1, 1, 9, 15), bars={}),
        MarketSnapshot(ts=datetime(2024, 1, 1, 9, 16), bars={}),
        MarketSnapshot(ts=datetime(2024, 1, 1, 9, 17), bars={}),
    ]

    assert filter_snapshots(snaps, start=datetime(2024, 1, 1, 9, 16), end=None) == snaps[1:]
    assert filter_snapshots(snaps, start=None, end=datetime(2024, 1, 1, 9, 16)) == snaps[:2]
    assert filter_snapshots(snaps, start=None, end=None) == snaps


def test_snapshots_to_frame_flattens_snapshots_sorted():
    t1 = datetime(2024, 1, 1, 9, 15)
    t2 = datetime(2024, 1, 1, 9, 16)
    snaps = [
        MarketSnapshot(ts=t2, bars={"AAA": Bar(ts=t2, open=1, high=2, low=0.5, close=1.5, volume=1)}),
        MarketSnapshot(
            ts=t1,
            bars={
                "BBB": Bar(ts=t1, open=10, high=11, low=9, close=10.5, volume=2),
                "AAA": Bar(ts=t1, open=1, high=2, low=0.5, close=1.5, volume=3),
            },
        ),
    ]
    df = snapshots_to_frame(snaps)

    assert isinstance(df, pl.DataFrame)
    assert df["ts"].to_list() == [t1, t1, t2]
    assert df["symbol"].to_list() == ["AAA", "BBB", "AAA"]
    assert df["close"].to_list() == pytest.approx([1.5, 10.5, 1.5])
