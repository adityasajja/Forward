"""Ports for options market."""

from __future__ import annotations

from typing import Any, Protocol


class OptionsMarketPort(Protocol):
    """Read and refresh the broker's option market catalog."""

    def session(self) -> Any | None:
        """Return the authenticated broker session, if one is stored.

        Returns:

            The active session, or ``None`` when the account is signed out.
        """

    def forward_catalog(self, underlying: str, exchange: str) -> Any:
        """Return listed option contracts for an underlying and exchange.

        Args:

            underlying: The option underlying symbol.

            exchange: The options exchange code.

        Returns:

            The matching dated contract catalog.
        """

    def spot_instrument(self, symbol: str, exchange: str) -> Any:
        """Return the configured spot instrument.

        Args:

            symbol: The spot index or equity symbol.

            exchange: The exchange where the spot instrument is listed.

        Returns:

            The resolved market instrument.
        """

    def bridge_topic(self, instrument: Any) -> str:
        """Return the feed topic for an instrument.

        Args:

            instrument: The resolved market instrument.

        Returns:

            The topic used by the live feed.
        """

    def refresh_contract_master(self) -> dict[str, Any]:
        """Refresh the broker contract catalog.

        Returns:

            A summary of refreshed contracts.
        """

    def contract_master_status(self) -> dict[str, Any]:
        """Return the cached contract catalog status.

        Returns:

            Availability and coverage for the stored contracts.
        """
