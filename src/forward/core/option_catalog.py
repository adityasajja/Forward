"""Queries for option contracts in a loaded instrument catalog."""

from __future__ import annotations

from datetime import date

from forward.core.enums import AssetClass, OptionExpiryType, OptionType
from forward.core.instrument_models import Instrument


class OptionContractCatalog:
    """Select option contracts from a dated instrument catalog."""

    def __init__(self, instruments: list[Instrument]) -> None:
        """Create a catalog from the supplied instruments.

        Args:
            instruments: Contracts available to the current data source.
        """
        self.instruments = tuple(instruments)

    def expiries(self, underlying: str, exchange: str, after: date) -> list[date]:
        """Return eligible expiry dates in ascending order."""
        values = sorted(
            {
                instrument.expiry
                for instrument in self.instruments
                if instrument.asset_class is AssetClass.OPTION
                and (instrument.underlying or "").upper() == underlying.upper()
                and instrument.exchange.upper() == exchange.upper()
                and instrument.expiry is not None
                and instrument.expiry >= after
            }
        )
        if not values:
            raise LookupError(f"no option expiries for {underlying} on {exchange} from {after}")
        return values

    def nearest_weekly_expiry(self, underlying: str, exchange: str, after: date) -> date:
        """Return the earliest explicitly classified weekly expiry."""
        return self._nearest_expiry_type(
            underlying, exchange, after, OptionExpiryType.WEEKLY
        )

    def nearest_monthly_expiry(self, underlying: str, exchange: str, after: date) -> date:
        """Return the nearest explicitly classified monthly expiry."""
        return self._nearest_expiry_type(
            underlying, exchange, after, OptionExpiryType.MONTHLY
        )

    def _nearest_expiry_type(
        self,
        underlying: str,
        exchange: str,
        after: date,
        expiry_type: OptionExpiryType,
    ) -> date:
        """Select an expiry only from contracts with matching explicit metadata."""
        matches = sorted(
            {
                instrument.expiry
                for instrument in self.instruments
                if instrument.asset_class is AssetClass.OPTION
                and (instrument.underlying or "").upper() == underlying.upper()
                and instrument.exchange.upper() == exchange.upper()
                and instrument.expiry_type is expiry_type
                and instrument.expiry is not None
                and instrument.expiry >= after
            }
        )
        if not matches:
            raise LookupError(
                f"no option contracts with explicit {expiry_type.value.lower()} expiry metadata "
                f"for {underlying} on {exchange} from {after}"
            )
        return matches[0]

    def nearest_option(
        self,
        underlying: str,
        exchange: str,
        expiry: date,
        option_type: OptionType,
        target_strike: float,
    ) -> Instrument:
        """Return the closest listed contract, with stable lower-strike ties."""
        choices = [
            instrument
            for instrument in self.instruments
            if instrument.asset_class is AssetClass.OPTION
            and (instrument.underlying or "").upper() == underlying.upper()
            and instrument.exchange.upper() == exchange.upper()
            and instrument.expiry == expiry
            and instrument.option_type is option_type
            and instrument.strike is not None
        ]
        if not choices:
            raise LookupError(f"no {option_type.value} contracts for {underlying} on {expiry}")
        return min(
            choices,
            key=lambda instrument: (
                abs(float(instrument.strike) - target_strike),
                float(instrument.strike),
                instrument.symbol,
            ),
        )
