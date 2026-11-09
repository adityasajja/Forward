"""The market overview reports only the official Nifty 50 index.

The index is downloaded from the broker when a session exists and cached; reading
it never needs a session. These tests use a fake client, so they prove the
plumbing (cache, freshness, source selection) but not IIFL's live response.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from forward.data import indices
from forward.data.cache_store import PanelDailyBarCache
from forward.data.market_intel_index_refresh import (
    LocalMarketIntelIndexRefreshAdapter,
)
from forward.data.market_intel_market_data import LocalMarketIntelDataAdapter
from forward.data.market_intel_snapshot import PickleMarketIntelSnapshotAdapter
from forward.market_intel.models import PROVENANCE_NIFTY50_INDEX
from forward.market_intel.service import MarketIntelService


def _market_intel_service(data_root) -> MarketIntelService:
    """Build a market-intelligence service over one isolated test data root."""
    from forward.wiring import broker_access

    def broker_client():
        return broker_access.get_authenticated_broker_client()

    return MarketIntelService(
        market_data=LocalMarketIntelDataAdapter(
            data_root, "NSEEQ", PanelDailyBarCache()
        ),
        index_refresh=LocalMarketIntelIndexRefreshAdapter(
            data_root,
            broker_client,
            base_url=FakeClient.base_url,
            contract_source="HTTP",
            intervals={"NIFTY 50": "1d", "INDIA VIX": "1d"},
        ),
        snapshot_cache=PickleMarketIntelSnapshotAdapter(
            data_root / "panel" / "market_intel.pkl"
        ),
    )


def _candles(end: date, n: int = 30) -> list[list]:
    days = pd.bdate_range(end=pd.Timestamp(end), periods=n)
    return [[d.strftime("%Y-%m-%d %H:%M:%S"), 25000 + i, 25050 + i, 24950 + i, 25010 + i, 0] for i, d in enumerate(days)]


class FakeClient:
    base_url = "https://example.invalid/v1"

    def __init__(self, end: date | None = None):
        self.end = end or date.today()
        self.calls: list[tuple] = []

    def historical_data(self, exchange, instrument_id, interval, from_date, to_date):
        self.calls.append((exchange, instrument_id, interval))
        return {"result": [{"candles": _candles(self.end)}]}


@pytest.fixture()
def root(tmp_path, monkeypatch):
    class ContractResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return [
                {
                    "underlyingInstrumentSymbol": "NIFTY 50",
                    "instrumentId": "999920000",
                    "exchange": "NSEEQ",
                },
                {
                    "underlyingInstrumentSymbol": "INDIA VIX",
                    "instrumentId": "999920019",
                    "exchange": "NSEFO",
                },
            ]

    monkeypatch.setattr(indices.httpx, "get", lambda *args, **kwargs: ContractResponse())
    return tmp_path


def test_sync_writes_a_cache_that_loads_without_a_session(root):
    client = FakeClient()
    assert indices.sync_index(
        client,
        root,
        name=indices.NIFTY_50,
        base_url=FakeClient.base_url,
        contract_source="HTTP",
        interval="1d",
    ) == "ok"
    assert client.calls == [("NSEEQ", "999920000", "1d")]

    frame = indices.load_index(root)  # no client involved
    assert frame is not None and len(frame) == 30


def test_a_second_sync_the_same_day_is_skipped(root):
    client = FakeClient()
    kwargs = {
        "name": indices.NIFTY_50,
        "base_url": FakeClient.base_url,
        "contract_source": "HTTP",
        "interval": "1d",
    }
    indices.sync_index(client, root, **kwargs)
    assert indices.sync_index(client, root, **kwargs) == "skip"
    assert len(client.calls) == 1


def test_a_failed_sync_keeps_the_previous_file(root):
    indices.sync_index(
        FakeClient(),
        root,
        name=indices.NIFTY_50,
        base_url=FakeClient.base_url,
        contract_source="HTTP",
        interval="1d",
    )

    class Broken(FakeClient):
        def historical_data(self, *a, **k):
            raise RuntimeError("session expired")

    assert indices.sync_index(
        Broken(),
        root,
        name=indices.NIFTY_50,
        base_url=FakeClient.base_url,
        contract_source="HTTP",
        interval="1d",
        force=True,
    ) == "fail"
    assert indices.load_index(root) is not None


def test_a_stale_cache_is_not_presented_as_the_latest(root):
    indices.sync_index(
        FakeClient(end=date.today() - timedelta(days=30)),
        root,
        name=indices.NIFTY_50,
        base_url=FakeClient.base_url,
        contract_source="HTTP",
        interval="1d",
    )
    assert indices.load_index(root) is None


def test_the_overview_prefers_the_official_index(root):
    indices.sync_index(
        FakeClient(),
        root,
        name=indices.NIFTY_50,
        base_url=FakeClient.base_url,
        contract_source="HTTP",
        interval="1d",
    )
    frame, provenance = _market_intel_service(root).get_benchmark_frame_and_provenance()
    assert provenance == PROVENANCE_NIFTY50_INDEX
    assert provenance.is_proxy is False
    assert frame is not None and float(frame["close"].iloc[-1]) > 1000  # an index level, not a ~267 ETF unit


def test_without_an_index_it_reports_the_benchmark_unavailable(root):
    etf_dir = root / "iifl_daily" / "NSEEQ"
    etf_dir.mkdir(parents=True)
    days = pd.bdate_range(end=pd.Timestamp(date.today()), periods=30)
    pd.DataFrame(
        {"ts": days, "open": 267.0, "high": 268.0, "low": 266.0, "close": 267.1, "volume": 1000.0}
    ).to_parquet(etf_dir / "NIFTYBEES-EQ.parquet")

    frame, provenance = _market_intel_service(root).get_benchmark_frame_and_provenance()
    assert frame is None
    assert provenance.kind == "UNAVAILABLE"
    assert provenance.is_proxy is None


def test_no_session_does_not_substitute_a_public_series(root, monkeypatch):
    from forward.core.errors import BrokerUnavailable
    from forward.wiring import broker_access

    def no_session():
        raise BrokerUnavailable(
            "no active IIFL session", status=401, code="no_broker_session"
        )

    monkeypatch.setattr(broker_access, "get_authenticated_broker_client", no_session)
    calls = []
    monkeypatch.setattr(indices, "sync_index", lambda *a, **k: calls.append(a) or "ok")

    service = _market_intel_service(root)
    service._maybe_refresh_index()
    assert calls == []
    service._maybe_refresh_index()
    assert calls == []


def test_a_failed_broker_download_does_not_substitute_a_public_series(root, monkeypatch):
    from forward.wiring import broker_access

    broker = FakeClient()
    monkeypatch.setattr(broker_access, "get_authenticated_broker_client", lambda: broker)
    calls = []
    monkeypatch.setattr(indices, "sync_index", lambda *a, **k: calls.append(a) or "fail")
    # Run the background work on the calling thread so the test can observe it.
    monkeypatch.setattr("threading.Thread.start", lambda self: self.run())

    _market_intel_service(root)._maybe_refresh_index()
    assert len(calls) == 2
