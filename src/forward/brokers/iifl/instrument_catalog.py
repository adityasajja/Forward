"""IIFL contract catalog adapter for symbol lookup and market data."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from forward.brokers.iifl.contracts import CACHE_DIR, InstrumentMaster
from forward.core.errors import ContractCatalogUnavailable
from forward.core.instrument_models import Instrument
from forward.core.market_models import InstrumentReference


class IiflInstrumentCatalogAdapter:
    """Search cached IIFL contracts and resolve their broker IDs."""

    def __init__(self, cache_dir: Path | str, cache_ttl_seconds: float) -> None:
        """Create the adapter.

        Args:
            cache_dir: Directory that stores IIFL contract files.
            cache_ttl_seconds: Time to reuse a loaded contract master.
        """
        self._cache_dir = Path(cache_dir)
        self._cache_ttl_seconds = cache_ttl_seconds
        self._masters: dict[str, tuple[float, InstrumentMaster]] = {}
        self._lock = threading.Lock()

    def contract_id(self, symbol: str, exchange: str) -> str:
        """Return the broker ID for an exact listed symbol.

        Args:
            symbol: Exact trading symbol.
            exchange: Exchange segment containing the symbol.

        Returns:
            Broker contract ID as text.
        """
        normalized_exchange = exchange.upper()
        return self._master(normalized_exchange).contract_id(
            symbol.upper(), normalized_exchange
        )

    def listed_symbol(self, symbol: str, exchange: str) -> str:
        """Confirm the exact symbol exists in one cached exchange catalog.

        Args:
            symbol: Exact trading symbol.
            exchange: Exchange segment containing the symbol.

        Returns:
            The catalog's exact symbol spelling.

        Raises:
            KeyError: If the catalog has no exact matching contract.
        """
        instrument = self._master(exchange.upper()).find(
            symbol.upper(), exchange=exchange.upper()
        )
        return instrument.symbol

    def instrument(self, symbol: str, exchange: str) -> Instrument:
        """Return the validated contract for an exact listed symbol.

        Args:
            symbol: Exact trading symbol.
            exchange: Exchange segment containing the contract.

        Returns:
            The contract with its lot size, multiplier, and broker identifier.

        Raises:
            KeyError: If the contract is missing from the catalog.
            ContractCatalogUnavailable: If the cached catalog cannot be read.
        """
        normalized_exchange = exchange.upper()
        return self._master(normalized_exchange).find(
            symbol.upper(), exchange=normalized_exchange
        )

    def search(
        self, query: str, exchange: str, limit: int
    ) -> list[InstrumentReference]:
        """Search one exchange's cached contracts.

        Args:
            query: Symbol text to search for.
            exchange: Exchange segment to search.
            limit: Maximum number of contracts to return.

        Returns:
            Matching symbols with their exchange and broker contract ID.
        """
        normalized_exchange = exchange.upper()
        matches = self._master(normalized_exchange).search(
            query.upper(), exchange=normalized_exchange, limit=limit
        )
        return [
            InstrumentReference(
                symbol=row.symbol,
                exchange=row.exchange,
                contract_id=str(row.conid),
            )
            for row in matches.itertuples(index=False)
        ]

    def _master(self, exchange: str) -> InstrumentMaster:
        """Return the loaded contract master while its cache is current."""
        now = time.monotonic()
        with self._lock:
            cached = self._masters.get(exchange)
            if cached is not None and now - cached[0] < self._cache_ttl_seconds:
                return cached[1]

            master = InstrumentMaster(cache_dir=self._cache_dir)
            try:
                master.load_cached([exchange])
            except Exception as exc:
                raise ContractCatalogUnavailable(str(exc)) from exc
            self._masters[exchange] = (now, master)
            return master


def build_iifl_instrument_catalog(cache_ttl_seconds: float) -> IiflInstrumentCatalogAdapter:
    """Create the IIFL catalog adapter with its configured cache location.

    Args:
        cache_ttl_seconds: Time to reuse a loaded contract master.

    Returns:
        The configured cached contract catalog adapter.
    """
    return IiflInstrumentCatalogAdapter(CACHE_DIR, cache_ttl_seconds)
