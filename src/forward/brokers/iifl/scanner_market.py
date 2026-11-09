"""IIFL adapter for exact-contract scanner market data."""

from __future__ import annotations

import pandas as pd

from forward.brokers.iifl.client import IiflClient
from forward.brokers.iifl.contracts import InstrumentMaster
from forward.brokers.iifl.feeds import candles_to_frame


class IiflScannerMarketAdapter:
    """Read daily bars from IIFL for exact listed trading symbols."""

    def __init__(self, client: IiflClient) -> None:
        """Create the adapter.

        Args:
            client: Authenticated IIFL client.
        """
        self._client = client
        self._masters: dict[str, InstrumentMaster] = {}

    def daily_bars(
        self, symbol: str, exchange: str, from_date: str, to_date: str
    ) -> pd.DataFrame:
        """Return daily bars for an exact contract match.

        Args:
            symbol: Exact trading symbol in the instrument master.
            exchange: Exchange segment containing the contract.
            from_date: First requested date in broker format.
            to_date: Last requested date in broker format.

        Returns:
            Daily OHLCV bars with parsed timestamps.

        Raises:
            KeyError: If the exact trading symbol is not listed.
        """
        master = self._master(exchange)
        instrument = master.find(symbol, exchange)
        response = self._client.historical_data(
            exchange, str(instrument.conid), "1d", from_date, to_date
        )
        return candles_to_frame(response)

    def _master(self, exchange: str) -> InstrumentMaster:
        """Return the contract index for one exchange segment.

        Args:
            exchange: Exchange segment whose contracts are needed.

        Returns:
            Loaded instrument master.
        """
        normalized_exchange = exchange.upper()
        master = self._masters.get(normalized_exchange)
        if master is None:
            master = InstrumentMaster(self._client)
            master.load_cached([normalized_exchange])
            self._masters[normalized_exchange] = master
        return master
