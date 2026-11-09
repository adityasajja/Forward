"""Intraday history adapter for IIFL Capital."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta

import pandas as pd

from forward.brokers.iifl.client import IiflClient
from forward.brokers.iifl.contracts import InstrumentMaster
from forward.brokers.iifl.feeds import candles_to_frame
from forward.core.session import IST


class IiflIntradayHistoryAdapter:
    """Resolve exact IIFL contracts and load their recent intraday candles."""

    def __init__(self, client_provider: Callable[[], IiflClient]) -> None:
        """Create the adapter.

        Args:
            client_provider: Return an authenticated IIFL client.
        """
        self._client_provider = client_provider

    def loaders_for(
        self,
        symbol_exchanges: Mapping[str, str],
        interval: str,
        lookback_days: int,
    ) -> Mapping[str, Callable[[], pd.DataFrame]]:
        """Build history loaders for the requested symbols.

        Args:
            symbol_exchanges: Symbols mapped to their exchange segments.
            interval: IIFL candle interval.
            lookback_days: Calendar days of history to request.

        Returns:
            Loaders keyed by canonical symbol.

        Raises:
            KeyError: If a requested symbol has no exact IIFL contract.
            RuntimeError: If the instrument master cannot be loaded.
        """
        client = self._client_provider()
        master = InstrumentMaster(client)
        exchanges = sorted({exchange.upper() for exchange in symbol_exchanges.values()})
        master.load_cached(exchanges)

        loaders: dict[str, Callable[[], pd.DataFrame]] = {}
        for symbol, exchange in symbol_exchanges.items():
            instrument = master.find(symbol, exchange)
            if instrument.conid is None:
                raise ValueError(
                    f"IIFL contract {symbol} on {exchange} has no instrument id"
                )
            loaders[symbol] = self._loader(
                client=client,
                exchange=exchange,
                instrument_id=str(instrument.conid),
                interval=interval,
                lookback_days=lookback_days,
            )
        return loaders

    @staticmethod
    def _loader(
        *,
        client: IiflClient,
        exchange: str,
        instrument_id: str,
        interval: str,
        lookback_days: int,
    ) -> Callable[[], pd.DataFrame]:
        """Return a closure that requests fresh candles when it is called."""

        def load() -> pd.DataFrame:
            to_date = datetime.now(IST)
            payload = client.historical_data(
                exchange=exchange,
                instrument_id=instrument_id,
                interval=interval,
                from_date=to_date - timedelta(days=lookback_days),
                to_date=to_date,
            )
            return candles_to_frame(payload)

        return load
