"""Ports for the related application workflows."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from forward.core.trade_signal_models import TradeSignal, TradeSignalSettings


class TradeSignalStoragePort(Protocol):
    """Load and save queued trade signals and their settings."""

    def load_queue(self) -> list[dict[str, Any]]:
        """Return saved trade signal records."""

    def save_queue(self, records: list[dict[str, Any]]) -> None:
        """Persist the current trade signal records."""

    def load_settings(self) -> dict[str, Any] | None:
        """Return saved settings, or ``None`` when no settings exist."""

    def save_settings(self, settings: Mapping[str, Any]) -> None:
        """Persist trade signal settings."""


class TradeSignalQueuePort(Protocol):
    """Manage pending and active trade signals."""

    def pending(self) -> list[TradeSignal]:
        """Return pending signals."""

    def add(self, signal: TradeSignal) -> None:
        """Add a signal to the queue."""

    def active_count(self) -> int:
        """Return the number of active signals."""

    def get(self, signal_id: str) -> TradeSignal | None:
        """Return one signal by ID."""

    def mark_active(self, signal_id: str, entry_id: str, stop_id: str, target_id: str) -> None:
        """Record the broker order IDs for an executed signal."""


class TradeSignalSettingsPort(Protocol):
    """Read trade signal settings."""

    def load(self) -> TradeSignalSettings:
        """Return the active trade signal settings."""


class ResearchSignalScannerPort(Protocol):
    """Find signal candidates produced by the research engine."""

    def scan_for_quant_signals(self, frames: dict[str, Any], max_candidates: int = 15) -> list[Any]:
        """Return the strongest research candidates for the supplied bars."""


class SignalExecutionAuditPort(Protocol):
    """Record completed signal approvals."""

    def append(
        self,
        *,
        actor: str,
        action: str,
        subject: str,
        detail: str | None = None,
    ) -> dict[str, Any]:
        """Append one signal execution event."""
