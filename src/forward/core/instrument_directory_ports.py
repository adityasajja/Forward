"""Ports for searching the local instrument directory."""

from __future__ import annotations

from typing import Any, Protocol


class InstrumentDirectoryPort(Protocol):
    """Read and refresh the configured instrument directory."""

    def search(
        self,
        query: str,
        *,
        exchange: str | None,
        asset_class: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Return matching instrument records in search order."""

    def get(self, symbol: str) -> dict[str, Any] | None:
        """Return one instrument record by symbol."""

    def exchanges(self) -> list[dict[str, Any]]:
        """Return the exchanges and instrument counts."""

    def status(self) -> dict[str, Any]:
        """Return the directory build and freshness details."""

    def refresh(self) -> dict[str, Any]:
        """Rebuild the instrument directory and return its build details."""

    def warm(self) -> None:
        """Start an off-request refresh of the instrument directory."""

    def universes(self) -> dict[str, list[str]]:
        """Return all named symbol universes."""

    def universe(self, name: str) -> list[str]:
        """Return the symbols in one named universe."""
