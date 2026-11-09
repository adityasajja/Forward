from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd

from forward.services import paper_health as health


def test_shared_history_prepared_once_and_next_refresh_is_fresh(monkeypatch):
    now = datetime(2026, 10, 12, 20, tzinfo=health.IST)
    frame = pd.DataFrame({'ts': pd.to_datetime(['2026-10-09', '2026-10-12', '2026-10-13']), 'close': [100, 101, 102]})
    history = SimpleNamespace(daily_frame=Mock(return_value=frame))
    behind = Mock(return_value=0)
    monkeypatch.setattr('forward.services.data_status.sessions_behind', behind)
    cal = SimpleNamespace(is_trading_day=lambda d: d.weekday() < 5)
    row = {
        'deployment_id': 'one',
        'config': {
            'symbols': ['TCS'],
            'exchange': 'NSEEQ',
            'timeframe': '1d',
            'max_open_positions': 10,
            'signal_basis': 'closed_bar',
        },
    }
    frames, prepared = {}, {}
    health_runner = SimpleNamespace(resolve_rules=lambda _: None)
    first = health._deployment_health(
        row, frames, now, cal, market_data=Mock(), history=history, runner=health_runner,
        signal_cache={}, prepared=prepared
    )
    second = health._deployment_health(
        {**row, 'deployment_id': 'two'}, frames, now, cal, market_data=Mock(), history=history,
        runner=health_runner, signal_cache={}, prepared=prepared
    )
    assert first == second
    assert first['data_through'] == '2026-10-12'
    assert history.daily_frame.call_count == behind.call_count == 1
    assert len(next(iter(prepared.values()))[0]) == 2  # future bar excluded
    history.daily_frame.return_value = frame.iloc[:1]
    behind.return_value = 1
    refreshed = health._deployment_health(
        row, {}, now, cal, market_data=Mock(), history=history, runner=health_runner,
        signal_cache={}, prepared={}
    )
    assert refreshed['stale'] == 1
    assert refreshed['data_through'] == '2026-10-09'
    assert history.daily_frame.call_count == behind.call_count == 2
