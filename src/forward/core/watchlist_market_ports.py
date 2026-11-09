"""Port for market data used by watchlists."""

from __future__ import annotations

from typing import Protocol

from forward.core.historical_market_data_ports import CachedInstrumentHistoryPort


class WatchlistInstrumentPort(CachedInstrumentHistoryPort, Protocol):
    """Resolve symbols needed to build watchlist quotes."""

    def resolve(self, symbol: str) -> str | None:
        """Return the catalog's symbol for a user input.

        Args:
            symbol: User-entered or exchange-qualified ticker.

        Returns:
            Canonical ticker, or None when unknown.
        """
