"""Application service for instrument directory queries."""

from __future__ import annotations

from typing import Any

from forward.core.instrument_directory_ports import InstrumentDirectoryPort


class InstrumentDirectoryService:
    """Serve instrument search and lookup through a directory port."""

    def __init__(self, directory: InstrumentDirectoryPort) -> None:
        """Create the service.

        Args:
            directory: Configured instrument directory adapter.
        """
        self._directory = directory

    def search(
        self,
        query: str,
        *,
        exchange: str | None = None,
        asset_class: str | None = None,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        """Find records that match a symbol or company name."""
        return self._directory.search(
            query,
            exchange=exchange,
            asset_class=asset_class,
            limit=limit,
        )

    def get(self, symbol: str) -> dict[str, Any] | None:
        """Find one instrument by its supplied symbol."""
        return self._directory.get(symbol)

    def exchanges(self) -> list[dict[str, Any]]:
        """List exchanges and their instrument counts."""
        return self._directory.exchanges()

    def status(self) -> dict[str, Any]:
        """Return index provenance and freshness."""
        return self._directory.status()

    def refresh(self) -> dict[str, Any]:
        """Refresh the instrument index."""
        return self._directory.refresh()

    def warm(self) -> None:
        """Start a background refresh of the instrument index."""
        self._directory.warm()

    def universes(self) -> dict[str, list[str]]:
        """Return all named symbol universes."""
        return self._directory.universes()

    def universe(self, name: str) -> list[str]:
        """Return a named symbol universe."""
        return self._directory.universe(name)
