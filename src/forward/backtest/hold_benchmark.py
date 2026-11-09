"""Fixed equal-capital sleeves; no rebalancing or hindsight-filled prices."""
import numpy as np
import pandas as pd


def hold_benchmark(frames: dict, start: str, end: str) -> dict:
    windows = {
        name: frame.loc[
            (frame.index >= pd.Timestamp(start))
            & (frame.index < pd.Timestamp(end) + pd.Timedelta(days=1))
        ]
        for name, frame in frames.items()
    }
    timeline = pd.DatetimeIndex(
        sorted({timestamp for window in windows.values() for timestamp in window.index})
    )
    if timeline.empty:
        raise ValueError("No valid prices in the benchmark period.")

    marks = {}
    covered = 0
    for name, window in windows.items():
        valid_entry = window.loc[
            np.isfinite(window["open"])
            & (window["open"] > 0)
            & np.isfinite(window["close"])
            & (window["close"] > 0)
        ]
        if valid_entry.empty:
            marks[name] = pd.Series(1.0, index=timeline)
            continue
        covered += 1
        # Each sleeve stays in cash until its first tradable open. Once bought,
        # every later mark must be present; carrying a stale price would hide a
        # missing quote in the benchmark result.
        first = valid_entry.index[0]
        close = window["close"].where(
            np.isfinite(window["close"]) & (window["close"] > 0)
        )
        held_marks = close.reindex(timeline[timeline >= first])
        if held_marks.isna().any():
            missing_date = held_marks.index[held_marks.isna()][0]
            raise ValueError(
                f"Missing close mark for benchmark symbol {name} on {missing_date}."
            )
        held_marks = held_marks / float(valid_entry.iloc[0]["open"])
        cash_period = pd.Series(1.0, index=timeline[timeline < first])
        marks[name] = pd.concat([cash_period, held_marks]).reindex(timeline)
    if not covered:
        raise ValueError('No valid prices in the benchmark period.')
    sleeves = pd.concat(marks, axis=1).sort_index()
    equity = sleeves.mean(axis=1)
    peak = equity.cummax().clip(lower=1.0)
    return {'total_return_pct': float((equity.iloc[-1] - 1) * 100),
            'max_drawdown_pct': float((1 - equity / peak).max() * 100),
            'symbols': covered, 'start': str(equity.index[0])[:10],
            'end': str(equity.index[-1])[:10],
            'assumption': 'Equal capital per stock, fractional shares, first available open to final close; no rebalancing, fees or slippage. Missing marks after entry make the benchmark unavailable. Uses current cached prices.'}
