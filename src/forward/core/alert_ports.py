"""Ports for the related application workflows."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, TypedDict

from forward.core.alert_config import IntelligentAlertConfig
from forward.core.market_models import Bar


class AlertQuote(TypedDict):
    """Validated quote fields used by price and indicator alerts."""

    ltp: float
    prev_close: float
    day_chg: float
    bar: Bar


class AlertQuotePort(Protocol):
    """Read current quotes for configured exchange symbols."""

    def quotes(self, symbols: Sequence[tuple[str, str]]) -> dict[str, AlertQuote]:
        """Return current quote fields keyed by symbol.

        Args:
            symbols: Symbol and exchange pairs requested by armed alert rules.

        Returns:
            Quote snapshots keyed by their requested symbol.
        """


class AlertHistoryPort(Protocol):
    """Read daily context frames used by indicator alert rules."""

    def context(self, symbol: str, current_bar: Bar) -> Any | None:
        """Return daily bars with the observed current bar appended.

        Args:
            symbol: The market symbol to read.
            current_bar: Observed current OHLCV values for the symbol.

        Returns:
            The daily context frame, or ``None`` when no history is available.
        """


class AlertStorePort(Protocol):
    """Persist alert rules and events."""

    def rules(self) -> list[Any]:
        """Return the stored alert rules."""

    def upsert(self, rule: Any) -> Any:
        """Insert or update one alert rule."""

    def remove(self, rule_id: str) -> bool:
        """Remove one stored rule and report whether it existed."""

    def events(self, limit: int = 100) -> list[Any]:
        """Return recent alert events, newest first."""

    def log(self, event: Any) -> None:
        """Append one fired alert event."""


class AlertHoldingsPort(Protocol):
    """Read held symbols for alert rules that depend on open positions."""

    def held_symbols(self) -> dict[str, dict[str, Any]]:
        """Return holdings keyed by canonical trading symbol."""


class IntelligentAlertConfigPort(Protocol):
    """Load and save intelligent alert settings."""

    def load(self) -> IntelligentAlertConfig:
        """Return the saved alert settings."""

    def save(self, config: IntelligentAlertConfig) -> IntelligentAlertConfig:
        """Save and return alert settings."""


class AlertDeliveryPort(Protocol):
    """Send an alert through one configured notification channel."""

    name: str

    def send(self, title: str, body: str) -> bool:
        """Send one alert and report whether the channel accepted it."""
