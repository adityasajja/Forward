"""Ports for the related application workflows."""

from __future__ import annotations

from typing import Any, Protocol

from forward.core.briefing_models import BriefingConfig


class BriefingStoragePort(Protocol):
    """Load briefing settings and save the most recent delivery record."""

    def load_config(self) -> BriefingConfig:
        """Return the stored briefing configuration."""

    def save_config(self, config: BriefingConfig) -> BriefingConfig:
        """Save and return briefing settings."""

    def last_sent(self) -> dict[str, Any] | None:
        """Return the latest sent briefing details, if present."""

    def record_sent(self, message: str, channel: str) -> None:
        """Record one successful briefing delivery."""
