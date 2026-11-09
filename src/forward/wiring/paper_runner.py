"""Build the paper runner from explicit application dependencies."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any

from forward.core.calendar_ports import MarketCalendarPort
from forward.core.historical_market_data_ports import (
    CachedInstrumentHistoryPort,
    CachedMarketFramesPort,
    IntradayHistoryPort,
)
from forward.core.live_broker_ports import LiveBrokerProviderPort
from forward.core.live_market_data_ports import MarketDataPort
from forward.core.market_tracking_ports import VwapLiquidityPort
from forward.core.order_ports import OrderInstrumentLookupPort, OrderStoragePort
from forward.core.paper_ledger_ports import PaperRunnerLedgerPort
from forward.core.paper_models import PaperPriceSourcePort
from forward.core.paper_runner_storage_ports import PaperRunnerStoragePort
from forward.core.portfolio_ports import PortfolioStoragePort
from forward.core.risk_ports import RiskStatePort
from forward.core.signal_context_ports import SignalContextEnrichmentPort
from forward.core.system_state_ports import SystemStateStoragePort
from forward.core.trade_journal_ports import TradeJournalStoragePort

if TYPE_CHECKING:
    from forward.services.runner import PaperRunner


def build_paper_runner(
    *,
    state_storage: SystemStateStoragePort,
    runner_storage: PaperRunnerStoragePort,
    order_storage: OrderStoragePort,
    risk_state: RiskStatePort,
    portfolio_storage: PortfolioStoragePort,
    sector_table: Mapping[str, str],
    instrument_lookup: OrderInstrumentLookupPort,
    journal_storage: TradeJournalStoragePort,
    ledger: PaperRunnerLedgerPort,
    broker_provider: LiveBrokerProviderPort,
    daily_history: CachedMarketFramesPort,
    instrument_history: CachedInstrumentHistoryPort,
    vwap_liquidity: VwapLiquidityPort,
    signal_context: SignalContextEnrichmentPort,
    calendar: MarketCalendarPort,
    price_source: PaperPriceSourcePort,
    interval_seconds: float,
    lookback_days: int,
    minimum_sleep_seconds: float,
    market_wait_seconds: float,
    symbol_retry_seconds: float,
    max_quote_age_seconds: float,
    average_volume_days: int,
    market_data: MarketDataPort | None = None,
    intraday_history: IntradayHistoryPort | None = None,
) -> PaperRunner:
    """Build the process paper runner from explicit dependencies.

    Args:
        state_storage: Durable runner state adapter.
        runner_storage: Deployment and strategy adapter.
        order_storage: Order lifecycle adapter.
        risk_state: Current risk policy and state.
        portfolio_storage: Portfolio data adapter.
        sector_table: Canonical symbol-to-industry catalog.
        instrument_lookup: Lookup for exact listed order instruments.
        journal_storage: Trade journal adapter.
        ledger: Paper account ledger.
        broker_provider: Authenticated live broker factory.
        daily_history: Cached daily bars.
        instrument_history: Instrument catalog and daily bars.
        vwap_liquidity: Prior-session liquidity data.
        signal_context: Signal annotation service.
        calendar: Exchange session calendar for strategy evaluation.
        price_source: Live quote source for paper execution.
        interval_seconds: Required runner pass cadence.
        lookback_days: Required history warmup window for each deployment.
        minimum_sleep_seconds: Required minimum loop sleep.
        market_wait_seconds: Delay while all deployments wait for market open.
        symbol_retry_seconds: Delay between feed subscription retries.
        max_quote_age_seconds: Maximum age for a paper execution quote.
        average_volume_days: Required history window used for liquidity checks.
        market_data: Live market feed for the running strategies.
        intraday_history: Exact intraday contract history.

    Returns:
        The configured process paper runner.

    """
    from forward.brokers.runner_venue_provider import (
        BrokerRunnerVenueProvider,
        build_opening_paper_venue,
        build_reversion_paper_venue,
    )
    from forward.core.runner_venue_ports import RunnerPaperExecutionProfile
    from forward.core.session import IST
    from forward.services.paper_prices import cached_adv_source
    from forward.services.runner import PaperRunner
    from forward.services.sector_opening import (
        evaluate as evaluate_opening,
    )
    from forward.services.sector_opening import (
        signal_key as opening_signal_key,
    )
    from forward.services.vwap_reversion import evaluate as evaluate_vwap
    from forward.services.vwap_reversion import quote as vwap_quote
    from forward.strategy.definition import parse_definition
    from forward.strategy.strategies.sector_opening import ENGINE as OPENING_ENGINE
    from forward.strategy.strategies.vwap_reversion import ENGINE as VWAP_ENGINE

    def quote_source(
        symbol: str,
        now: datetime,
        *,
        max_age_seconds: float,
    ) -> dict[str, Any] | None:
        """Read one VWAP quote from the configured market feed.

        Args:
            symbol: Exact symbol requested by the strategy.
            now: Current exchange time used to check quote freshness.

        Returns:
            A valid two-sided quote, or ``None`` when the feed has no quote.
        """
        if market_data is None:
            raise RuntimeError("VWAP paper deployment requires a market data adapter")
        return vwap_quote(
            market_data,
            symbol,
            now,
            max_age_seconds=max_age_seconds,
        )

    def market_clock() -> datetime:
        """Return the current time in the exchange timezone.

        Returns:
            The current exchange-local time.
        """
        return datetime.now(IST)

    def vwap_profile(paper_venue: Any, definition: Any) -> Any:
        """Build the VWAP paper venue from configured market dependencies.

        Args:
            paper_venue: Shared venue that supplies the configured fill ratio.

        Returns:
            A VWAP paper venue.
        """
        if not isinstance(definition, dict):
            raise ValueError("VWAP venue requires its saved strategy definition")
        parameters = (definition.get("params") or {}).get("vwap")
        if not isinstance(parameters, dict):
            raise ValueError("VWAP venue requires saved params.vwap settings")

        def configured_quote_source(symbol: str, now: datetime) -> dict[str, Any] | None:
            return quote_source(
                symbol,
                now,
                max_age_seconds=parameters["max_quote_age_seconds"],
            )

        return build_reversion_paper_venue(
            paper_venue=paper_venue,
            market_data=market_data,
            vwap_liquidity=vwap_liquidity,
            instrument_lookup=instrument_lookup,
            quote_source=configured_quote_source,
            clock=market_clock,
            parameters=parameters,
        )

    def opening_profile(paper_venue: Any, _definition: Any) -> Any:
        """Build the opening-strategy paper venue from live prices.

        Args:
            paper_venue: Shared venue to configure for this strategy.

        Returns:
            An opening-strategy paper venue.
        """
        return build_opening_paper_venue(
            paper_venue=paper_venue,
            price_source=price_source,
        )

    def vwap_evaluator(loop: Any, now: datetime) -> list[dict[str, Any]]:
        """Run the VWAP lifecycle with the loop's configured market ports.

        Args:
            loop: Deployment loop being evaluated.
            now: Current exchange time.

        Returns:
            Orders created during this evaluation.
        """
        if loop.market_data is None:
            raise RuntimeError("VWAP deployment requires a market data adapter")
        return evaluate_vwap(
            loop,
            now,
            loop.market_data,
            loop.vwap_liquidity,
            calendar=loop.calendar,
        )

    def opening_evaluator(loop: Any, now: datetime) -> list[dict[str, Any]]:
        """Run the opening strategy with the loop's configured market feed.

        Args:
            loop: Deployment loop being evaluated.
            now: Current exchange time.

        Returns:
            Orders created during this evaluation.
        """
        if loop.market_data is None:
            raise RuntimeError("Opening-sector deployment requires a market data adapter")
        return evaluate_opening(loop, now, loop.market_data, calendar=loop.calendar)

    def subscribe_strategy_feed(loop: Any, feed: MarketDataPort) -> None:
        """Subscribe to index and sector topics pinned by the strategy.

        Args:
            loop: Deployment whose saved definition selects the topics.
            feed: Live market feed that accepts index subscriptions.
        """
        definition, _ = parse_definition(loop.row.get("definition"))
        pinned = definition["params"]["universe"]
        topics = [(pinned["index"]["name"], pinned["index"]["topic"])]
        topics.extend((name, sector["topic"]) for name, sector in pinned["sectors"].items())
        feed.ensure_indices(tuple(topics))

    venue_provider = BrokerRunnerVenueProvider(
        broker_provider=broker_provider,
        price_source=price_source,
        instrument_lookup=instrument_lookup,
        risk_state=risk_state,
        adv_source=cached_adv_source(instrument_history, days=average_volume_days),
        max_quote_age_seconds=max_quote_age_seconds,
        paper_profiles={
            VWAP_ENGINE: RunnerPaperExecutionProfile(
                venue_builder=vwap_profile,
                evaluator=vwap_evaluator,
                skip_history_warmup=True,
                clock_sensitive=True,
                signal_key_builder=None,
                refuse_resting_fills=True,
                cancel_resting_entries=False,
                feed_subscriber=subscribe_strategy_feed,
            ),
            OPENING_ENGINE: RunnerPaperExecutionProfile(
                venue_builder=opening_profile,
                evaluator=opening_evaluator,
                skip_history_warmup=True,
                clock_sensitive=True,
                signal_key_builder=opening_signal_key,
                refuse_resting_fills=False,
                cancel_resting_entries=True,
                feed_subscriber=subscribe_strategy_feed,
            ),
        },
    )

    runner = PaperRunner(
        ledger=ledger,
        interval_seconds=interval_seconds,
        lookback_days=lookback_days,
        minimum_sleep_seconds=minimum_sleep_seconds,
        market_wait_seconds=market_wait_seconds,
        symbol_retry_seconds=symbol_retry_seconds,
        state_storage=state_storage,
        runner_storage=runner_storage,
        order_storage=order_storage,
        risk_state=risk_state,
        portfolio_storage=portfolio_storage,
        sector_table=sector_table,
        instrument_lookup=instrument_lookup,
        journal_storage=journal_storage,
        market_data=market_data,
        intraday_history=intraday_history,
        daily_history=daily_history,
        vwap_liquidity=vwap_liquidity,
        signal_context=signal_context,
        calendar=calendar,
        venue_provider=venue_provider,
    )
    return runner
