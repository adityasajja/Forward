import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import Mock

from forward.brokers.iifl.live_feed import IiflLiveFeedAdapter
from forward.services.runner import PaperRunner


def test_disconnected_bridge_is_reused_not_replaced(monkeypatch):
    session = SimpleNamespace(user_session="current", is_expired=lambda: False)
    adapter = IiflLiveFeedAdapter(
        session_store=SimpleNamespace(load=lambda: session),
        instrument_catalog=SimpleNamespace(contract_id=lambda symbol, exchange: "1"),
        tls_verify=True,
        bridge_host="bridge.test",
        bridge_port=8883,
        bridge_connect_timeout_seconds=8,
        bridge_reconnect_min_seconds=1,
        bridge_reconnect_max_seconds=30,
    )
    bridge = SimpleNamespace(is_connected=False, session=session)
    adapter._bridge = bridge
    factory = Mock()
    monkeypatch.setattr("forward.brokers.iifl.bridge.BridgeClient", factory)
    for _ in range(30):
        assert not adapter.ensure_connected(
            on_feed=Mock(), on_index=Mock(), on_error=Mock(), feed_topics=[], index_topics=[]
        )
    factory.assert_not_called()
    bridge.is_connected = True
    assert adapter.ensure_connected(
        on_feed=Mock(), on_index=Mock(), on_error=Mock(), feed_topics=[], index_topics=[]
    )


def test_failed_bridge_is_stopped_and_retry_is_throttled(monkeypatch):
    session = SimpleNamespace(is_expired=lambda: False)
    adapter = IiflLiveFeedAdapter(
        session_store=SimpleNamespace(load=lambda: session),
        instrument_catalog=SimpleNamespace(contract_id=lambda symbol, exchange: "1"),
        tls_verify=True,
        bridge_host="bridge.test",
        bridge_port=8883,
        bridge_connect_timeout_seconds=8,
        bridge_reconnect_min_seconds=1,
        bridge_reconnect_max_seconds=30,
    )
    bridge = Mock()
    bridge.connect.side_effect = TimeoutError("no connack")
    factory = Mock(return_value=bridge)
    monkeypatch.setattr("forward.brokers.iifl.bridge.BridgeClient", factory)
    def ensure_connection():
        return adapter.ensure_connected(
            on_feed=Mock(), on_index=Mock(), on_error=Mock(), feed_topics=[], index_topics=[]
        )
    assert not ensure_connection()
    assert not ensure_connection()
    factory.assert_called_once()
    bridge.disconnect.assert_called_once()
    assert adapter._bridge is None


def test_clock_worker_runs_while_generic_pass_is_blocked():
    async def check():
        runner = PaperRunner.__new__(PaperRunner)
        runner._stop = threading.Event()
        runner._task = runner._clock_task = None
        runner._default_interval = 1
        runner._lock = threading.Lock()
        blocked, release, evaluated = threading.Event(), threading.Event(), threading.Event()
        loop = SimpleNamespace(
            row={"definition": {"engine_key": "nifty_opening_sector_momentum"}},
            deployment_id="clock-test", bind=Mock(), poll_resting_orders=Mock(),
            evaluate=lambda **kwargs: evaluated.set(),
        )
        runner._loops = {"clock-test": loop}

        def slow_pass():
            blocked.set()
            assert release.wait(5)

        runner.pass_once = slow_pass
        runner.start()
        try:
            assert await asyncio.to_thread(blocked.wait, 2)
            evaluated.clear()
            assert await asyncio.to_thread(evaluated.wait, 2)
            assert not release.is_set()
            assert runner._clock_owned(loop)
        finally:
            release.set()
            await runner.stop()
        assert runner._clock_task is None

    asyncio.run(check())
