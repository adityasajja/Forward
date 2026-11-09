"""Port for broker contract catalogs."""

from __future__ import annotations

from typing import Protocol

from forward.core.market_models import InstrumentReference


class BrokerInstrumentCatalogPort(Protocol):
    """Resolve and search exact instruments in the broker catalog."""

    def listed_symbol(self, symbol: str, exchange: str) -> str:
        """Confirm a listed symbol and return its exact spelling.

        Args:
            symbol: Exact trading symbol to find.
            exchange: Exchange segment that lists the symbol.

        Returns:
            The exact symbol spelling in the broker catalog.
        """

    def contract_id(self, symbol: str, exchange: str) -> str:
        """Return the broker contract ID for an exact listed symbol.

        Args:
            symbol: Exact trading symbol to find.
            exchange: Exchange segment that lists the symbol.

        Returns:
            Broker contract ID as text.
        """

    def search(self, query: str, exchange: str, limit: int) -> list[InstrumentReference]:
        """Search the broker catalog on one exchange.

        Args:
            query: Text to match against symbols.
            exchange: Exchange segment to search.
            limit: Maximum number of results.

        Returns:
            Matching listed instruments.
        """
