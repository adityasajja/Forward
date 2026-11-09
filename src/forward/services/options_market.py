"""Prepare option-market inputs for F&O use cases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from forward.core.instrument_models import Instrument
from forward.core.options_market_ports import OptionsMarketPort


@dataclass(frozen=True)
class OptionsMarketInputs:
    """Market inputs required to start a forward paper run."""

    session: Any
    catalog: Any
    spot: Instrument


class OptionsMarketService:
    """Prepare broker market data needed by options workflows."""

    def __init__(self, market: OptionsMarketPort) -> None:
        """Create the service with its market-data adapter.

        Args:
            market: The adapter that reads and refreshes option market data.
        """
        self._market = market

    def prepare_forward(
        self,
        *,
        session: Any,
        underlying: str,
        exchange: str,
        spot_symbol: str,
        spot_exchange: str,
    ) -> OptionsMarketInputs:
        """Load all broker inputs required by one forward run.

        Args:
            underlying: The underlying used to select listed option contracts.
            exchange: The exchange that lists those option contracts.
            spot_symbol: The symbol used for spot-price observations.
            spot_exchange: The exchange that lists the spot instrument.

        Returns:
            The authenticated session, option catalog, and spot instrument.

        Raises:
            LookupError: Required market contracts are unavailable.
        """
        return OptionsMarketInputs(
            session=session,
            catalog=self._market.forward_catalog(underlying, exchange),
            spot=self._market.spot_instrument(spot_symbol, spot_exchange),
        )

    def session(self) -> Any | None:
        """Return the authenticated session required by broker requests.

        Returns:
            The stored broker session, or ``None`` when the account is signed out.
        """
        return self._market.session()

    def bridge_topic(self, instrument: Instrument) -> str:
        """Resolve the feed topic used to subscribe to a spot instrument.

        Args:
            instrument: The resolved spot instrument.

        Returns:
            The market-data topic.
        """
        return self._market.bridge_topic(instrument)

    def refresh_contract_master(self) -> dict[str, Any]:
        """Refresh the exchange contract catalog.

        Returns:
            A summary of the refreshed option coverage.
        """
        return self._market.refresh_contract_master()

    def contract_master_status(self) -> dict[str, Any]:
        """Return the stored exchange contract coverage.

        Returns:
            A summary of available option contracts.
        """
        return self._market.contract_master_status()
