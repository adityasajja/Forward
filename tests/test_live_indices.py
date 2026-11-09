"""Live Nifty / India VIX from the broker's index feed."""

import pytest

from forward.api.stream import TickBroadcaster
from forward.brokers.iifl.codec import decode_index_packet
from forward.core.market_models import LiveMarketQuote
from forward.data.observed_bars import ObservedBars
from tests.market_data_fakes import (
    InMemoryLiveMarketFeed,
    InMemoryTickCandleAggregation,
)

# Real packets captured on 1 Oct 2026 (the market was closed, so each carries the last
# session's values), with the figures the exchange itself reported for that session.
NIFTY = bytes.fromhex("00e08d93362200000000001d8422006400000000")
VIX = bytes.fromhex("13e08da634020000000000f40e02001027000000")


def test_nifty_packet_decodes_to_the_sessions_close_and_the_one_before():
    tick = decode_index_packet(NIFTY)
    assert tick.ltp == pytest.approx(22421.95)
    assert tick.close == pytest.approx(22620.45)


def test_vix_packet_uses_its_own_price_divisor():
    tick = decode_index_packet(VIX)
    assert tick.ltp == pytest.approx(14.455)  # divisor 10000, not 100
    assert tick.close == pytest.approx(13.49)


@pytest.mark.parametrize("bad", [b"", NIFTY[:10], bytes(20)])
def test_a_malformed_packet_is_rejected_not_misread(bad):
    with pytest.raises(ValueError):
        decode_index_packet(bad)


def test_broadcaster_keeps_the_latest_quote_per_index():
    b = TickBroadcaster(
        InMemoryLiveMarketFeed(), InMemoryTickCandleAggregation(), ObservedBars()
    )
    b._index_topics = {"nseeq/999920000": "NIFTY 50", "nsefo/999920019": "INDIA VIX"}
    b._on_index_feed("nseeq/999920000", _quote(NIFTY))
    b._on_index_feed("nsefo/999920019", _quote(VIX))
    b._on_index_feed("nseeq/unknown", _quote(NIFTY))  # not subscribed: ignored

    quotes = b.latest_indices()
    assert set(quotes) == {"NIFTY 50", "INDIA VIX"}
    assert quotes["NIFTY 50"]["chg_pct"] == pytest.approx((22421.95 / 22620.45 - 1) * 100, abs=0.01)
    assert quotes["INDIA VIX"]["age_s"] >= 0


def _quote(packet: bytes) -> LiveMarketQuote:
    """Map the decoded broker packet to the application quote model."""
    tick = decode_index_packet(packet)
    return LiveMarketQuote(
        last=tick.ltp,
        quantity=tick.last_traded_quantity,
        volume=tick.traded_volume,
        high=tick.high,
        low=tick.low,
        open=tick.open,
        close=tick.close,
        traded_at=tick.last_traded_time,
    )


def test_ensure_indices_without_a_session_reports_unavailable(monkeypatch):
    b = TickBroadcaster(
        InMemoryLiveMarketFeed(is_connected=False),
        InMemoryTickCandleAggregation(),
        ObservedBars(),
    )
    monkeypatch.setattr(b, "_ensure_live_feed", lambda: False)
    assert b.ensure_indices() is False
    assert b.latest_indices() == {}


def test_ensure_indices_subscribes_once(monkeypatch):
    feed = InMemoryLiveMarketFeed()
    b = TickBroadcaster(feed, InMemoryTickCandleAggregation(), ObservedBars())
    monkeypatch.setattr(b, "_ensure_live_feed", lambda: True)
    assert b.ensure_indices() and b.ensure_indices()
    assert feed.index_subscriptions == [["nseeq/999920000", "nsefo/999920019"]]


def test_live_endpoint_shape(monkeypatch):
    from fastapi.testclient import TestClient

    from forward.api import deps, stream
    from forward.api.deps import anonymous_principal
    from forward.api.main import app

    class FakeBroadcaster:
        def ensure_indices(self):
            return True

        def latest_indices(self):
            return {
                "NIFTY 50": {"ltp": 22400.0, "prev_close": 22000.0, "chg": 400.0, "chg_pct": 1.82, "age_s": 1.0, "recv": 0},
            }

    monkeypatch.setattr(stream, "get_broadcaster", lambda: FakeBroadcaster())
    app.dependency_overrides[deps.get_principal] = lambda: anonymous_principal()
    try:
        body = TestClient(app).get("/api/v1/market-intel/live").json()
    finally:
        app.dependency_overrides.clear()
    assert body["available"] is True
    assert body["nifty"]["ltp"] == 22400.0
    assert body["vix"] is None  # no quote yet: the page keeps the daily number
    assert isinstance(body["market_open"], bool)
