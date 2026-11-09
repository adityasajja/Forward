"""Port for chart market data."""

from __future__ import annotations

from typing import Any, Protocol

from forward.core.market_models import InstrumentReference, QuoteBatch


class ChartMarketDataPort(Protocol):
    """Read historical candles and current quotes for listed instruments."""

    def historical_candles(
        self,
        instrument: InstrumentReference,
        interval: str,
        from_date: str,
        to_date: str,
    ) -> list[dict[str, Any]]:
        """Return candles for one listed instrument.

        Args:
            instrument: Listed instrument to read.
            interval: Candle interval.
            from_date: First date to include.
            to_date: Last date to include.

        Returns:
            OHLCV rows ordered by the market data source.
        """

    def quote_payload(self, instruments: list[InstrumentReference]) -> QuoteBatch:
        """Request current quotes for listed instruments.

        Args:
            instruments: Instruments to quote.

        Returns:
            Source response for the API boundary to validate.
        """
