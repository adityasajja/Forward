"""Manage price alerts and evaluate them against live market data."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from forward.alerts.engine import check as check_alerts
from forward.alerts.models import AlertEvent, AlertRule
from forward.core.alert_ports import (
    AlertDeliveryPort,
    AlertHistoryPort,
    AlertQuotePort,
    AlertStorePort,
)
from forward.core.session import is_market_session_open


class AlertService:
    """Manage saved alert rules and run alert checks."""

    def __init__(
        self,
        store: AlertStorePort,
        history: AlertHistoryPort,
        channel_factory: Callable[[], Sequence[AlertDeliveryPort]],
    ) -> None:
        """Create the alert service.

        Args:
            store: Port that saves alert rules and fired events.
            history: Port that reads daily context for indicator alerts.
            channel_factory: Returns notification channels in delivery order.
        """
        self._store = store
        self._history = history
        self._channel_factory = channel_factory

    def rules(self) -> list[AlertRule]:
        """Return all saved alert rules."""
        return self._store.rules()

    def save_rule(self, values: dict[str, Any]) -> AlertRule:
        """Validate and save one alert rule.

        Args:
            values: Fields from the rule editor.

        Returns:
            The saved rule.
        """
        return self._store.upsert(AlertRule.model_validate(values))

    def arm_rule(self, rule_id: str, armed: bool) -> AlertRule | None:
        """Change whether a saved rule can fire.

        Args:
            rule_id: ID of the rule to update.
            armed: Whether alert checks should evaluate the rule.

        Returns:
            The updated rule, or ``None`` if the ID is unknown.
        """
        rule = next((item for item in self._store.rules() if item.id == rule_id), None)
        if rule is None:
            return None
        values = {**rule.model_dump(), "armed": armed}
        return self._store.upsert(AlertRule.model_validate(values))

    def remove_rule(self, rule_id: str) -> bool:
        """Remove one rule and report whether it existed.

        Args:
            rule_id: ID of the rule to remove.

        Returns:
            Whether a saved rule was removed.
        """
        return self._store.remove(rule_id)

    def events(self, limit: int) -> list[AlertEvent]:
        """Return recent alert events.

        Args:
            limit: Maximum number of events to return.

        Returns:
            Events ordered newest first.
        """
        return self._store.events(limit)

    def check(self, quotes: AlertQuotePort) -> tuple[bool, list[AlertEvent]]:
        """Evaluate armed rules and send notifications for rules that fire.

        Args:
            quotes: Port that reads quotes for the current check.

        Returns:
            Whether the market is open and the events created by this check.
        """
        events = check_alerts(
            self._store,
            quotes,
            self._history,
            list(self._channel_factory()),
        )
        return is_market_session_open(), events

    def send_test(self) -> str:
        """Send a test message through the first channel that accepts it.

        Returns:
            Name of the accepting channel, or ``"none"`` when none accepts it.
        """
        sent_on = "none"
        for channel in self._channel_factory():
            if channel.send("Forward test", "alerts are wired — you will get firing rules here."):
                sent_on = channel.name
                break
        return sent_on
