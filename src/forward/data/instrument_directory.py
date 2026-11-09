"""Local cache adapter for instrument directory queries."""

from __future__ import annotations

from typing import Any

from forward.core.instrument_directory_ports import InstrumentDirectoryPort
from forward.data.instrument_catalog import InstrumentMaster


class LocalInstrumentDirectoryAdapter(InstrumentDirectoryPort):
    """Expose the local instrument index through its application port."""

    def __init__(self, master: InstrumentMaster) -> None:
        """Create the adapter.

        Args:
            master: Index built from the configured market data cache.
        """
        self._master = master

    def search(
        self,
        query: str,
        *,
        exchange: str | None,
        asset_class: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Return matching records as transport-neutral mappings."""
        return [
            record.as_dict()
            for record in self._master.search(
                query, exchange=exchange, asset_class=asset_class, limit=limit
            )
        ]

    def get(self, symbol: str) -> dict[str, Any] | None:
        """Return one record as a mapping when the symbol exists."""
        record = self._master.get(symbol)
        return record.as_dict() if record is not None else None

    def exchanges(self) -> list[dict[str, Any]]:
        """Return the exchange summary from the local index."""
        return self._master.exchanges()

    def status(self) -> dict[str, Any]:
        """Return local index provenance and freshness."""
        return self._master.status()

    def refresh(self) -> dict[str, Any]:
        """Rebuild the index and return its summary."""
        snapshot = self._master.refresh(force=True)
        return {
            "rebuilt": True,
            "symbols": len(snapshot.records),
            "build_seconds": round(snapshot.build_seconds, 2),
            "built_at": snapshot.built_at,
        }

    def warm(self) -> None:
        """Start the instrument index refresh on its background thread."""
        self._master.warm()

    def universes(self) -> dict[str, list[str]]:
        """Return all cached index memberships."""
        return self._master.universes()

    def universe(self, name: str) -> list[str]:
        """Return one cached index membership list."""
        return self._master.universe(name)
