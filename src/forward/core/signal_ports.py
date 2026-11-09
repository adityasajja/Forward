"""Market-data operations used by signal scans."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from forward.core.instrument_models import Instrument
from forward.core.market_models import MarketQuote
from forward.core.portfolio_models import Holding


class SignalMarketPort(Protocol):
    """Read holdings, contracts, quotes, and history for signal scans."""

    def holdings(self, exchange: str) -> tuple[Sequence[Holding], str | None]:
        """Return settled holdings and any broker read error.

        Args:

            exchange: Exchange segment used for the holding instruments.

        Returns:

            Holdings and an error message, if the broker read failed.

        """

    def resolve(self, symbols: Sequence[str], exchange: str) -> Mapping[str, Instrument]:
        """Resolve the exact contracts available on one exchange.

        Args:

            symbols: Trading symbols to resolve.

            exchange: Exchange segment that holds the contracts.

        Returns:

            Instruments keyed by the requested symbol.

        """

    def quotes(self, instruments: Sequence[Instrument]) -> Mapping[tuple[str, str], MarketQuote]:
        """Return current quotes keyed by exchange and instrument id.

        Args:

            instruments: Contracts to quote.

        Returns:

            Quotes keyed by their exchange and instrument id.

        """

    def daily_bars(self, instrument: Instrument, lookback_days: int) -> Any:
        """Return daily history for a contract.

        Args:

            instrument: Contract whose daily bars are requested.

            lookback_days: Calendar days of history to request.

        Returns:

            Daily OHLCV data.

        """
