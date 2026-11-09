"""Coordinate briefing configuration, market data, and delivery."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from forward.briefing import build_brief
from forward.core.alert_ports import (
    AlertDeliveryPort,
)
from forward.core.briefing_models import BriefingConfig
from forward.core.briefing_ports import (
    BriefingStoragePort,
)
from forward.core.historical_market_data_ports import CachedMarketFramesPort


class BriefingService:
    """Build and send a morning briefing through injected ports."""

    def __init__(
        self,
        storage: BriefingStoragePort,
        market_frames: CachedMarketFramesPort,
        channels: Callable[[], Sequence[AlertDeliveryPort]],
    ) -> None:
        """Create the briefing service.

        Args:
            storage: Adapter for briefing settings and send history.
            market_frames: Reader for cached daily market bars.
            channels: Returns notification channels in delivery order.
        """
        self._storage = storage
        self._market_frames = market_frames
        self._channels = channels

    def get_config(self) -> BriefingConfig:
        """Return the saved briefing settings."""
        return self._storage.load_config()

    def save_config(self, values: dict[str, Any]) -> BriefingConfig:
        """Persist validated briefing settings.

        Args:
            values: Settings supplied by the briefing editor.

        Returns:
            The saved settings.
        """
        config = BriefingConfig.model_validate(values)
        return self._storage.save_config(config)

    def last_sent(self) -> dict[str, Any] | None:
        """Return the most recent delivery details."""
        return self._storage.last_sent()

    def preview(self) -> tuple[str, dict[str, Any]]:
        """Build a message from the current config and daily market frames."""
        return build_brief(self.get_config(), self._market_frames.frames("NSEEQ"))

    def send(self) -> tuple[str, dict[str, Any]]:
        """Send a briefing to the first channel that accepts it.

        Returns:
            The message and its delivery status.
        """
        config = self.get_config()
        message, stats = build_brief(config, self._market_frames.frames("NSEEQ"))
        if not config.send_enabled:
            return message, {"sent_on": "disabled", "stats": stats}

        for channel in self._channels():
            if channel.send("Forward morning brief", message):
                self._storage.record_sent(message, channel.name)
                return message, {"sent_on": channel.name, "stats": stats}
        return message, {"sent_on": "none", "stats": stats}
