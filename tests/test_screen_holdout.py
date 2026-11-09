import numpy as np
import pytest

from forward.compute.holdout import training_window


def test_selection_input_ignores_every_held_out_price():
    dates = np.arange('2020-01-01', '2022-01-20', dtype='datetime64[D]')
    close = np.ones((len(dates), 2))
    before, eligible, split = training_window(close, dates)
    changed = close.copy()
    changed[split['train_bars']:] = np.nan
    after, other_eligible, other_split = training_window(changed, dates)
    np.testing.assert_array_equal(before, after)
    np.testing.assert_array_equal(eligible, other_eligible)
    assert split == other_split
    assert split['train_end'] < split['test_start']


def test_training_eligibility_and_minimum_windows():
    dates = np.arange('2020-01-01', '2022-01-20', dtype='datetime64[D]')
    close = np.ones((len(dates), 2))
    close[:300, 1] = np.nan
    _, eligible, _ = training_window(close, dates)
    assert eligible.tolist() == [True, False]
    with pytest.raises(ValueError):
        training_window(close[:300], dates[:300])


def test_detailed_run_respects_both_boundaries_with_warmup():
    from types import SimpleNamespace

    import pandas as pd

    from forward.backtest.config import BacktestRunConfig
    from forward.backtest.runner import BacktestRunner
    from forward.data.backtest_market_data import frames_to_backtest_feed

    dates = pd.bdate_range('2020-01-01', periods=500)
    prices = 100 + np.sin(np.arange(500) / 10) * 10
    frame = pd.DataFrame({'ts': dates, 'open': prices, 'high': prices + 1,
                          'low': prices - 1, 'close': prices, 'volume': 100000})
    market_data = SimpleNamespace(
        load_frames=lambda symbols, exchange, timeframe, end: ({'INFY': frame}, []),
        build_feed=frames_to_backtest_feed,
    )
    runner = BacktestRunner(
        BacktestRunConfig(
            symbols=['INFY'], start=str(dates[250].date()),
            end=str(dates[350].date()), params={'fast':10,'slow':60}
        ),
        market_data=market_data,
        max_persisted_trades=20_000,
        max_curve_points=20_000,
    )
    _, frames, warmup = runner.build_feed()
    assert frames['INFY'].index.max() == dates[350]
    assert warmup == (frames['INFY'].index < dates[250]).sum()
    outcome = runner.execute()
    assert outcome.equity[0][0] >= dates[250]
    assert outcome.equity[-1][0] <= dates[350]
