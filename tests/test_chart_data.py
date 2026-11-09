"""Chart routes require a broker session, even when daily cache data exists."""

from __future__ import annotations

from fastapi.testclient import TestClient

from forward.api.main import app

LOOPBACK = ("127.0.0.1", 51234)


def test_the_route_requires_a_broker_session_without_an_explicit_cache_mode(fresh_env, master):
    client = TestClient(app, client=LOOPBACK)
    r = client.get("/candles", params={"symbol": "RELIANCE-EQ", "interval": "1d", "from_date": "01-Mar-2024", "to_date": "31-Mar-2024"})
    assert r.status_code == 401, r.text


def test_the_route_still_needs_the_broker_for_intraday(fresh_env, master):
    client = TestClient(app, client=LOOPBACK)
    assert client.get("/candles", params={"symbol": "RELIANCE-EQ", "interval": "5m"}).status_code >= 400
