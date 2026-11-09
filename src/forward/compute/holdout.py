"""Chronological holdout boundaries, fixed before any parameter selection."""
from __future__ import annotations

import numpy as np


def training_window(close: np.ndarray, dates: np.ndarray) -> tuple[np.ndarray, np.ndarray, dict]:
    """Split close history in time order and select names using training data.

    Args:
        close: Close prices in ``[dates, symbols]`` order.
        dates: One date for each row in ``close``.

    Returns:
        Training closes for eligible symbols, the full symbol eligibility mask,
        and the train/test date and bar boundaries.

    Raises:
        ValueError: If dates and closes do not match or the history is too short.
    """
    if close.ndim != 2 or close.shape[0] != len(dates):
        raise ValueError("close must have one row for each date.")
    if close.shape[1] == 0:
        raise ValueError("close must contain at least one symbol.")
    split = int(len(dates) * 0.7)
    if split < 250 or len(dates) - split < 100:
        raise ValueError('Screening needs 250 training bars and 100 held-out bars.')
    # Eligibility depends exclusively on the training period.
    eligible = np.isfinite(close[:split]).sum(axis=0) >= 250
    return close[:split, eligible], eligible, {
        'train_start': str(dates[0])[:10], 'train_end': str(dates[split - 1])[:10],
        'test_start': str(dates[split])[:10], 'test_end': str(dates[-1])[:10],
        'train_bars': split, 'test_bars': len(dates) - split,
    }
