"""BSE / NSE-SME history: split adjustment and the daily-job schedule."""

from datetime import datetime

import pandas as pd

from forward.data import market_history as mh


def _sessions(closes: list[float], *, split_at: int | None = None, ratio: float = 0.1) -> pd.DataFrame:
    """Raw sessions as the exchange publishes them: from the split day on, prices are on the new scale."""
    rows = []
    for i, raw in enumerate(closes):
        scale = ratio if split_at is not None and i >= split_at else 1.0
        c = raw * scale
        prev = closes[i - 1] * (ratio if split_at is not None and i - 1 >= split_at else 1.0) if i else c  # unadjusted last close
        rows.append({"date": pd.Timestamp("2026-01-01") + pd.Timedelta(days=i), "open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "prev": prev, "volume": 1000})
    return pd.DataFrame(rows)


def test_a_ten_for_one_split_is_adjusted_so_history_has_no_cliff():
    g = _sessions([100, 101, 102, 103, 104, 105], split_at=3, ratio=0.1)
    out = mh._adjust(g)
    returns = out["close"].pct_change().abs().dropna()
    assert returns.max() < 0.05  # no 90% drop left in the series
    assert out["volume"].iloc[0] == 10000  # volume is scaled the other way


def test_an_ordinary_series_is_left_alone():
    g = _sessions([100, 102, 99, 103, 101, 104])
    out = mh._adjust(g)
    assert list(out["close"]) == [100, 102, 99, 103, 101, 104]


def test_the_daily_job_runs_after_the_close_and_catches_up_after_downtime(tmp_path):
    friday_evening = datetime(2026, 10, 2, 19, 0)
    assert mh.due(tmp_path, friday_evening) is True  # never ran
    mh.mark_done(tmp_path, friday_evening)
    assert mh.due(tmp_path, friday_evening) is False  # already today
    assert mh.due(tmp_path, datetime(2026, 10, 3, 19, 0)) is False  # Saturday: no session
    assert mh.due(tmp_path, datetime(2026, 10, 6, 19, 0)) is True  # 4 days later


def test_search_offers_nse_before_bse(tmp_path):
    from forward.data.instrument_catalog import InstrumentMaster

    for exchange, symbol in (("BSEEQ", "ACMEBSE"), ("NSEEQ", "ACMENSE"), ("NSESME", "ACMESME")):
        folder = tmp_path / "iifl_daily" / exchange
        folder.mkdir(parents=True)
        pd.DataFrame({"ts": [pd.Timestamp("2026-01-01 09:15")], "open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1]}).to_parquet(folder / f"{symbol}.parquet")
    found = InstrumentMaster(tmp_path).search("ACME")
    assert [r.exchange for r in found] == ["NSEEQ", "NSESME", "BSEEQ"]
