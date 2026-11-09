"""Notifications for critical reconciliation results."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

from forward.core.alert_ports import AlertDeliveryPort
from forward.services.reconcile import SEVERITY_CRITICAL, ReconciliationReport

logger = logging.getLogger("forward.services.reconciliation_notifications")


class ReconciliationNotifier:
    """Send critical reconciliation reports through configured alert channels."""

    def __init__(self, channel_factory: Callable[[], Sequence[AlertDeliveryPort]]) -> None:
        """Create the notifier with a channel factory.

        Args:
            channel_factory: Loads the configured channels when a report is sent.
        """
        self._channel_factory = channel_factory

    def __call__(self, report: ReconciliationReport) -> None:
        """Send a critical report, continuing across failed channels.

        Args:
            report: Reconciliation result to notify about.
        """
        if report.severity != SEVERITY_CRITICAL:
            return
        try:
            self._send(report)
        except Exception:  # noqa: BLE001 - notification failure must not fail a check
            logger.exception("could not send the reconciliation notification")

    def _send(self, report: ReconciliationReport) -> None:
        """Format the report and try channels in their configured order."""
        lines = [
            f"severity: {report.severity}",
            f"scope: {report.scope}",
            f"mismatches: {report.mismatch_count} ({len(report.critical)} critical)",
        ]
        for difference in report.critical[:10]:
            lines.append(f"- {difference.scope}/{difference.key}: {difference.detail}")
        if report.incomparable:
            lines.append(
                "not compared: " + ", ".join(item["scope"] for item in report.incomparable)
            )
        body = "\n".join(lines)
        for channel in self._channel_factory():
            try:
                if channel.send("Forward reconciliation: critical mismatch", body):
                    break
            except Exception:  # noqa: BLE001 - one dead channel must not stop the others
                logger.exception("a notification channel failed")
