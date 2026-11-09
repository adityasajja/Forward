"""Instrument catalog use cases for market data routes."""

from __future__ import annotations

from forward.core.broker_catalog_ports import BrokerInstrumentCatalogPort
from forward.core.market_models import InstrumentReference


class InstrumentCatalogService:
    """Search listed instruments and resolve broker contract IDs."""

    def __init__(self, catalog: BrokerInstrumentCatalogPort) -> None:
        """Create the catalog service.

        Args:
            catalog: Adapter that reads the broker's listed contracts.
        """
        self._catalog = catalog

    def contract_id(self, symbol: str, exchange: str) -> str:
        """Resolve the exact broker contract ID for a symbol.

        Args:
            symbol: Exact trading symbol.
            exchange: Exchange segment containing the symbol.

        Returns:
            Broker contract ID as text.
        """
        return self._catalog.contract_id(symbol, exchange)

    def search(
        self, query: str, exchange: str, limit: int
    ) -> list[InstrumentReference]:
        """Search listed instruments on one exchange.

        Args:
            query: Symbol text to search for.
            exchange: Exchange segment to search.
            limit: Maximum number of matches to return.

        Returns:
            Matching instruments with broker IDs.
        """
        return self._catalog.search(query, exchange, limit)
