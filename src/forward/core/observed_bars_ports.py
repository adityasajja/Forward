"""Ports for building bars from live quote observations."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol


class ObservedBarsPort(Protocol):
    """Collect live quote observations into completed intraday bars."""

    def add(
        self,
        symbol: str,
        price: float,
        volume: float,
        observed_at: float,
        received_at: float,
        *,
        index: bool = False,
    ) -> None:
        """Add one quote observation to the active session."""

    def snapshot(self, now: datetime) -> dict[str, Any]:
        """Return completed bars observed before ``now``."""
