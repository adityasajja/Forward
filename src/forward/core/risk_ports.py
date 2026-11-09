"""Ports for risk controls and live-order policy."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, Protocol

from forward.core.order_ports import OrderInstrumentLookupPort


class RiskStateStoragePort(Protocol):
    """Read and change durable risk-control values."""

    def read(self, defaults: Mapping[str, Any]) -> dict[str, Any]:
        """Read selected values from one consistent store snapshot.

        Args:
            defaults: State names and values used when a row is missing.

        Returns:
            Every requested state name and its stored or default value.
        """

    def update(
        self,
        defaults: Mapping[str, Any],
        change: Callable[[dict[str, Any]], dict[str, Any]],
        *,
        actor: str,
        reason: str | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Apply a state change atomically and return before and after values.

        Args:
            defaults: State names and values used when rows are missing.
            change: Function that returns the values to store.
            actor: User or service making the change.
            reason: Audit reason for the change.

        Returns:
            The state before and after the change.
        """


class RiskPositionPort(Protocol):
    """Expose the position values used by order risk checks."""

    @property
    def quantity(self) -> float:
        """Return the signed position size."""

    @property
    def last_price(self) -> float:
        """Return the latest price used to value the position."""


class RiskPortfolioPort(Protocol):
    """Expose account values used by order risk checks."""

    @property
    def equity(self) -> float:
        """Return the current account equity."""

    @property
    def gross_exposure(self) -> float:
        """Return the account's total absolute exposure."""

    @property
    def positions(self) -> Mapping[str, RiskPositionPort]:
        """Return the account's positions by symbol."""

    def position(self, symbol: str) -> RiskPositionPort:
        """Return the position for a symbol, including an empty position."""


class LiveRiskPolicyPort(Protocol):
    """Provide the deployment's live-order protections."""

    @property
    def live_default_max_order_notional(self) -> float:
        """Return the maximum order value used when no user cap is set."""

    @property
    def enforce_market_hours(self) -> bool:
        """Return whether live orders must be inside NSE market hours."""


class RiskStatePort(Protocol):
    """Read or enforce the current platform risk controls."""

    def snapshot(self) -> Any:
        """Return the current kill switch, mode, and limits."""

    def gate(
        self,
        *,
        portfolio: RiskPortfolioPort,
        instruments: OrderInstrumentLookupPort,
        now: Callable[[], datetime] | None = None,
    ) -> Any:
        """Build the order risk gate from current controls."""

    def kill_switch_engaged(self) -> bool:
        """Return whether new orders are currently blocked."""
