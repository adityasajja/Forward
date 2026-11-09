"""Ports for market data status."""

from __future__ import annotations

from typing import Any, Protocol

from forward.core.market_models import MarketDataStatusSnapshot


class DatabaseHealthPort(Protocol):
    """Read the latest database health result."""

    def is_available(self) -> bool:
        """Return whether the database answered the latest probe."""


class DailyHistoryStatusPort(Protocol):
    """Read coverage details for the local daily history cache."""

    def status(self) -> dict[str, Any]:
        """Return cache location, exchange coverage, and symbol count."""


class ContractCacheStatusPort(Protocol):
    """Read coverage details for locally cached broker contracts."""

    def status(self) -> dict[str, Any]:
        """Return cache location, segment coverage, and contract count."""


class MarketDataStatusPort(Protocol):
    """Read facts used by the market data freshness report."""

    def snapshot(self) -> MarketDataStatusSnapshot:
        """Return the latest stored dates and source details."""
