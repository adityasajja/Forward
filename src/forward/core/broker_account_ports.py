"""Ports for broker account reads."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from forward.core.market_models import BrokerReadResult
from forward.core.portfolio_models import Position


class BrokerAccountReadPort(Protocol):
    """Read account sections from one broker session."""

    @property
    def session_identity(self) -> str:
        """Return a stable, non-secret key for reads from this session."""

    def section(self, name: str, *, include_quotes: bool = True) -> BrokerReadResult:
        """Read one account section.

        Args:
            name: Account section, such as positions or holdings.
            include_quotes: Whether holdings should include current quotes.

        Returns:
            Broker records and any broker error message.
        """


class BrokerPositionReadPort(Protocol):
    """Read normalized positions from a broker account."""

    def positions(self) -> Sequence[Position]:
        """Return current open and closed positions."""
