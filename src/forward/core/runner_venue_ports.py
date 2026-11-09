"""Ports for selecting execution adapters used by the deployment runner."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class RunnerPaperExecutionProfile:
    """Venue and evaluator for a paper strategy with a special run loop.

    Attributes:
        venue_builder: Builds the strategy's paper venue from the shared one.
        evaluator: Runs one strategy pass with its configured dependencies.
        skip_history_warmup: Whether this strategy loads bars another way.
        clock_sensitive: Whether the clock worker owns evaluation and order sync.
        signal_key_builder: Builds a strategy-specific key for one signal.
        refuse_resting_fills: Whether accepted entries must never fill later.
        cancel_resting_entries: Whether resting entry remainders are canceled.
        feed_subscriber: Adds feed topics required by this strategy.
    """

    venue_builder: Callable[[Any, Mapping[str, Any]], Any]
    evaluator: Callable[[Any, datetime], list[dict[str, Any]]]
    skip_history_warmup: bool
    clock_sensitive: bool
    signal_key_builder: Callable[[Any, str], str] | None
    refuse_resting_fills: bool
    cancel_resting_entries: bool
    feed_subscriber: Callable[[Any, Any], None] | None


@dataclass(frozen=True)
class RunnerVenueSelection:
    """Execution venue and optional portfolio selected for one deployment.

    Attributes:
        venue: Adapter used to submit orders for the deployment.
        portfolio: Live portfolio adapter, or ``None`` when paper uses its ledger.
        profile: Selected paper profile, or ``None`` for ordinary/live execution.
    """

    venue: Any
    portfolio: Any | None = None
    profile: RunnerPaperExecutionProfile | None = None


class RunnerVenueProviderPort(Protocol):
    """Build the runner's default and deployment-specific execution adapters."""

    def paper_venue(self) -> Any:
        """Return the shared paper venue.

        Returns:
            The paper execution venue.
        """

    def profile_for(self, engine_key: str | None) -> RunnerPaperExecutionProfile | None:
        """Return a registered paper profile for one engine.

        Args:
            engine_key: Strategy engine key saved in the definition.

        Returns:
            The registered profile, or ``None`` for rule-based strategies.
        """

    def for_deployment(
        self,
        *,
        mode: str | None,
        engine_key: str | None,
        paper_venue: Any,
        strategy_config: Mapping[str, Any],
    ) -> RunnerVenueSelection:
        """Select execution adapters for one saved deployment.

        Args:
            mode: Saved execution mode, such as ``PAPER`` or ``LIVE``.
            engine_key: Optional strategy engine that needs a special venue.
            paper_venue: Shared paper venue used by ordinary deployments.
            strategy_config: Saved strategy definition used by special paper
                venue builders. It has no inferred values.

        Returns:
            The selected venue and a portfolio adapter when the venue needs one.
        """
