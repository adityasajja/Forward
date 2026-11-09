"""IIFL adapter for chart candles and quotes."""

from __future__ import annotations

from typing import Any

from forward.brokers.iifl.client import IiflClient
from forward.brokers.iifl.responses import broker_error, rows
from forward.core.market_models import InstrumentReference, QuoteBatch


class IiflChartMarketAdapter:
    """Read chart market data through an authenticated IIFL client."""

    def __init__(self, client: IiflClient) -> None:
        """Create the chart market adapter.

        Args:
            client: Authenticated IIFL client for this request.
        """
        self._client = client

    def historical_candles(
        self,
        instrument: InstrumentReference,
        interval: str,
        from_date: str,
        to_date: str,
    ) -> list[dict[str, Any]]:
        """Read and normalize IIFL candles for one instrument.

        Args:
            instrument: Listed instrument with its IIFL contract ID.
            interval: Requested candle interval.
            from_date: First date in IIFL's date format.
            to_date: Last date in IIFL's date format.

        Returns:
            OHLCV rows in the chart API's candle shape.
        """
        payload = self._client.historical_data(
            instrument.exchange,
            instrument.contract_id,
            interval,
            from_date,
            to_date,
        )
        return [
            {
                "ts": candle[0],
                "open": candle[1],
                "high": candle[2],
                "low": candle[3],
                "close": candle[4],
                "volume": candle[5],
            }
            for candle in payload["result"][0]["candles"]
        ]

    def quote_payload(self, instruments: list[InstrumentReference]) -> QuoteBatch:
        """Request IIFL quotes for the listed instruments.

        Args:
            instruments: Instruments to quote.

        Returns:
            Normalized quote rows and any broker error text.
        """
        legs = [
            (instrument.exchange, instrument.contract_id) for instrument in instruments
        ]
        payload = self._client.market_quotes(legs)
        return QuoteBatch(rows=rows(payload), error=broker_error(payload))
