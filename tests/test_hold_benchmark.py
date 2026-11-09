import pandas as pd
import pytest

from forward.backtest.hold_benchmark import hold_benchmark


def test_fixed_sleeves_do_not_rebalance_and_include_first_open_gap():
    dates = pd.date_range('2025-01-01', periods=3)
    frames = {'A': pd.DataFrame({'open':[100,100,100], 'close':[80,100,200]},index=dates),
              'B': pd.DataFrame({'open':[100,100,100], 'close':[100,100,50]},index=dates)}
    result = hold_benchmark(frames,'2025-01-01','2025-01-03')
    assert result['total_return_pct'] == pytest.approx(25)
    assert result['max_drawdown_pct'] == pytest.approx(10)


def test_dates_are_clipped_and_missing_first_quote_stays_cash():
    dates = pd.date_range('2025-01-01', periods=4)
    frames = {'A':pd.DataFrame({'open':[1,100,100,1], 'close':[1000,100,110,10000]},index=dates),
              'B':pd.DataFrame({'open':[1,float('nan'),100,1], 'close':[1000,float('nan'),100,10000]},index=dates)}
    result = hold_benchmark(frames,'2025-01-02','2025-01-03')
    assert result['total_return_pct'] == pytest.approx(5)
    assert result['start']=='2025-01-02'
    assert result['end']=='2025-01-03'


def test_stock_without_quotes_keeps_its_capital_in_cash():
    dates = pd.date_range('2025-01-01', periods=2)
    frames = {'A':pd.DataFrame({'open':[100,100], 'close':[100,200]},index=dates),
              'B':pd.DataFrame({'open':[float('nan')]*2, 'close':[float('nan')]*2},index=dates)}
    result = hold_benchmark(frames,'2025-01-01','2025-01-02')
    assert result['total_return_pct'] == pytest.approx(50)
    assert result['symbols'] == 1
