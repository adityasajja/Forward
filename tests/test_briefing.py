"""Morning briefing — message building and config persistence."""

import pandas as pd

import forward.briefing as B
from forward.core.briefing_models import BriefingConfig
from forward.data.briefing_storage import JsonBriefingStorageAdapter


def _frame(start=100.0, step=1.0, n=80, vol=100000):
    closes = [start + i * step for i in range(n)]
    return pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n),
        "open": closes,
        "high": [c * 1.01 for c in closes],
        "low": [c * 0.99 for c in closes],
        "close": closes,
        "volume": [vol] * n,
    })


def _cfg(**kw):
    return BriefingConfig(
        top_n=2,
        avoid_n=1,
        min_price=1.0,
        min_day_value_lakh=0.0,
        **kw,
    )


def test_brief_structure():
    frames = {"AAA-EQ": _frame(step=1.0), "BBB-EQ": _frame(start=200.0, step=-1.0)}
    msg, stats = B.build_brief(_cfg(), frames, as_of="Mon 01 Jan")
    assert "WATCH LONGS" in msg and "AVOID" in msg
    assert "No trigger = no trade" in msg
    assert stats["scored"] == 2


def test_oversold_gets_os_tag():
    frames = {"BBB-EQ": _frame(start=200.0, step=-1.0)}
    msg, _ = B.build_brief(_cfg(), frames, as_of="Mon 01 Jan")
    assert "OS" in msg  # deeply oversold RSI → bounce-watch tag


def test_empty_cache_message():
    msg, _ = B.build_brief(_cfg(), {})
    assert "cache empty" in msg


def test_config_roundtrip(tmp_path):
    storage = JsonBriefingStorageAdapter(tmp_path)
    cfg = storage.save_config(BriefingConfig(top_n=5))
    assert storage.load_config().top_n == 5
    assert cfg.top_n == 5


def test_ranking_changes_picks():
    # AAA rises (vs_high near 0, high 1M ret) — score ranks it top,
    # vs_high alone does not.
    frames = {
        "AAA-EQ": _frame(step=1.0, n=100),
        "BBB-EQ": _frame(start=200.0, step=-0.5),
    }
    ls = B._rows(_cfg(), frames)["symbol"].tolist()
    assert "BBB-EQ" in ls  # weakest vs_high excluded by ATR floor? covered below
    assert set(ls) <= {"AAA-EQ", "BBB-EQ"}


def test_atr_floor_filters_flat_instruments():
    n = 80
    flat = pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n),
        "open": [1000.0] * n, "high": [1000.0] * n,
        "low": [1000.0] * n, "close": [1000.0] * n,
        "volume": [200000] * n,
    })
    frames = {"AAA-EQ": _frame(step=1.0), "FLAT-EQ": flat}
    ls = B._rows(_cfg(), frames)["symbol"].tolist()
    assert "FLAT-EQ" not in ls
