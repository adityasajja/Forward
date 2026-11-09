"""Build broker and paper venues for the continuous deployment runner."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from forward.brokers.live_deployment import LiveDeploymentVenue
from forward.brokers.paper_venue import PaperVenue
from forward.brokers.portfolio import BrokerPortfolio
from forward.brokers.reversion_venue import ReversionPaperVenue
from forward.config.costs import indian_intraday_costs
from forward.core.live_broker_ports import LiveBrokerProviderPort
from forward.core.live_market_data_ports import MarketDataPort
from forward.core.market_tracking_ports import VwapLiquidityPort
from forward.core.order_ports import OrderInstrumentLookupPort
from forward.core.paper_models import PaperPriceSourcePort, TickPrice
from forward.core.risk_ports import RiskStatePort
from forward.core.runner_venue_ports import (
    RunnerPaperExecutionProfile,
    RunnerVenueSelection,
)


def build_reversion_paper_venue(
    *,
    paper_venue: PaperVenue,
    market_data: MarketDataPort | None,
    vwap_liquidity: VwapLiquidityPort,
    instrument_lookup: OrderInstrumentLookupPort,
    quote_source: Callable[[str, datetime], dict[str, Any] | None],
    clock: Callable[[], datetime],
    parameters: Mapping[str, Any],
) -> ReversionPaperVenue:
    """Build the quote-only paper venue used by the VWAP engine.

    Args:
        paper_venue: Shared paper venue, which supplies the fill ratio.
        market_data: Live feed required by the VWAP paper workflow.
        vwap_liquidity: Source of verified prior-session average volume.
        instrument_lookup: Lookup for exact listed contracts.
        quote_source: Reader for a validated two-sided quote.
        clock: Market clock used to check quote freshness.
        parameters: Required VWAP settings from the pinned strategy version.

    Returns:
        The quote-only VWAP venue.

    Raises:
        RuntimeError: If the live market feed is not configured.
    """
    if market_data is None:
        raise RuntimeError("VWAP paper deployment requires a market data adapter")

    def executable(symbol: str, exchange: str) -> TickPrice | None:
        """Build a tick from the current executable quote."""
        del exchange
        now = clock()
        quote = quote_source(symbol, now)
        if quote is None:
            return None
        bid = quote["bid"]
        ask = quote["ask"]
        exchange_time = quote["exchange_time"]
        return TickPrice(
            (bid + ask) / 2,
            exchange_time,
            (now - exchange_time).total_seconds(),
            bid=bid,
            ask=ask,
        )

    def prior_adv(symbol: str, exchange: str) -> float | None:
        """Read the verified prior-session average volume."""
        del exchange
        return vwap_liquidity.liquidity(clock().date()).get(symbol, {}).get("adv")

    return ReversionPaperVenue(
        prices=executable,
        instruments=instrument_lookup,
        costs=indian_intraday_costs(),
        adv=prior_adv,
        fill_ratio=paper_venue.fill_ratio,
        parameters=parameters,
    )


def build_opening_paper_venue(
    *, paper_venue: PaperVenue, price_source: PaperPriceSourcePort
) -> PaperVenue:
    """Build the fresh-tick venue used by opening-strategy paper runs.

    Args:
        paper_venue: Shared paper venue to configure.
        price_source: Live price source without a daily-cache lookup.

    Returns:
        The opening-strategy paper venue.
    """
    return replace(
        paper_venue,
        prices=price_source,
        costs=indian_intraday_costs(),
        max_tick_age_seconds=paper_venue.max_tick_age_seconds,
    )


@dataclass
class BrokerRunnerVenueProvider:
    """Create execution adapters from the runner's configured ports.

    Attributes:
        broker_provider: Factory for the authenticated broker client.
        price_source: Live price source used by paper execution.
        instrument_lookup: Lookup for exact order instruments.
        risk_state: Current live-execution controls.
        adv_source: Source of average daily volume for the shared paper venue.
        paper_profiles: Strategy execution profiles keyed by saved engine key.
    """

    broker_provider: LiveBrokerProviderPort
    price_source: PaperPriceSourcePort
    instrument_lookup: OrderInstrumentLookupPort
    risk_state: RiskStatePort
    adv_source: Callable[[str, str], float | None]
    max_quote_age_seconds: float
    paper_profiles: Mapping[str, RunnerPaperExecutionProfile]

    def paper_venue(self) -> PaperVenue:
        """Build the shared paper venue with its price and volume sources.

        Returns:
            The default venue used for simulated execution.
        """
        return PaperVenue(
            prices=self.price_source,
            instruments=self.instrument_lookup,
            adv=self.adv_source,
            max_tick_age_seconds=self.max_quote_age_seconds,
        )

    def profile_for(
        self, engine_key: str | None
    ) -> RunnerPaperExecutionProfile | None:
        """Return the paper profile registered for one strategy engine.

        Args:
            engine_key: Strategy engine key from the saved definition.

        Returns:
            The matching profile, or ``None`` when the engine uses generic rules.
        """
        if engine_key is None:
            return None
        return self.paper_profiles.get(engine_key)

    def for_deployment(
        self,
        *,
        mode: str | None,
        engine_key: str | None,
        paper_venue: PaperVenue,
        strategy_config: Mapping[str, Any],
    ) -> RunnerVenueSelection:
        """Select a venue for the saved strategy and execution mode.

        Args:
            mode: Saved execution mode.
            engine_key: Strategy engine key, when the strategy needs a special
                paper execution model.
            paper_venue: Shared paper venue.

        Returns:
            The venue and optional portfolio required by the deployment.

        Raises:
            RuntimeError: If the live broker cannot provide a readable position
                book or the deployment mode is not supported.
        """
        if mode == "LIVE":
            broker = self.broker_provider()
            venue = LiveDeploymentVenue(
                broker=broker,
                instruments=self.instrument_lookup,
                prices=paper_venue.prices,
                max_tick_age_seconds=self.max_quote_age_seconds,
            )
            venue.may_transmit = lambda: self.risk_state.snapshot().live
            portfolio = BrokerPortfolio(broker)
            _ = portfolio.positions
            if not portfolio.readable:
                raise RuntimeError("Live broker positions are unavailable")
            return RunnerVenueSelection(venue=venue, portfolio=portfolio)

        if mode != "PAPER":
            raise RuntimeError(f"Unsupported deployment execution mode: {mode!r}")

        profile = self.profile_for(engine_key)
        if profile is None:
            return RunnerVenueSelection(venue=paper_venue)
        return RunnerVenueSelection(
            venue=profile.venue_builder(paper_venue, strategy_config),
            profile=profile,
        )
