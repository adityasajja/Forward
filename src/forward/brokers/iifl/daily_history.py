"""IIFL adapter for reading daily historical candles."""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from forward.brokers.iifl.client import IiflClient
from forward.brokers.iifl.feeds import candles_to_frame
from forward.core.instrument_models import Instrument
from forward.core.session import IST


def load_daily(
    client: IiflClient,
    instrument: Instrument,
    lookback_days: int,
) -> pd.DataFrame:
    """Load daily bars from IIFL for one resolved instrument.

    Args:
        client: Authenticated IIFL client.
        instrument: Resolved contract with an IIFL instrument ID.
        lookback_days: Calendar days requested from the broker.

    Returns:
        The broker's daily OHLCV bars.

    Raises:
        ValueError: If the instrument has no IIFL ID.
        RuntimeError: If IIFL rejects or cannot serve the request.
    """
    if instrument.conid is None:
        raise ValueError(f"{instrument.symbol} has no IIFL instrument ID")
    to_date = datetime.now(IST)
    payload = client.historical_data(
        exchange=instrument.exchange,
        instrument_id=str(instrument.conid),
        interval="1d",
        from_date=to_date - timedelta(days=lookback_days),
        to_date=to_date,
    )
    return candles_to_frame(payload)
