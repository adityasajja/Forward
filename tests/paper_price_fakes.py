"""Explicit paper price sources for isolated service tests."""

from typing import Any

from forward.core.runner_venue_ports import RunnerVenueSelection
from tests.paper_venue_factory import configured_paper_venue as PaperVenue


def no_live_price(symbol: str, exchange: str) -> None:
    """Return no quote when a test does not exercise live valuation.

    Args:
        symbol: Instrument symbol requested by the ledger.
        exchange: Listing exchange requested by the ledger.

    Returns:
        No price measurement.
    """
    del symbol, exchange
    return None


class StaticRunnerVenueProvider:
    """Build one paper venue for runner tests that do not use a special engine."""

    def __init__(self, *, prices: Any, instruments: Any) -> None:
        """Save the explicit test price and instrument adapters.

        Args:
            prices: Price source used to value simulated fills.
            instruments: Listed instrument lookup used by the venue.
        """
        self._prices = prices
        self._instruments = instruments

    def paper_venue(self) -> PaperVenue:
        """Build the test venue.

        Returns:
            A paper venue configured with the supplied test adapters.
        """
        return PaperVenue(
            prices=self._prices,
            instruments=self._instruments,
            max_tick_age_seconds=None,
        )

    def profile_for(self, engine_key: str | None) -> None:
        """Return no custom runner profile for generic strategy tests.

        Args:
            engine_key: Saved strategy engine key.

        Returns:
            No custom profile.
        """
        del engine_key
        return None

    def for_deployment(
        self,
        *,
        mode: str,
        engine_key: str | None,
        paper_venue: PaperVenue,
    ) -> RunnerVenueSelection:
        """Return the test paper venue for an ordinary paper deployment.

        Args:
            mode: Execution mode under test.
            engine_key: Strategy engine key, unused by these tests.
            paper_venue: Venue created by :meth:`paper_venue`.

        Returns:
            The test paper venue.
        """
        del mode, engine_key
        return RunnerVenueSelection(venue=paper_venue)
