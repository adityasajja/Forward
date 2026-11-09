"""Store briefing settings and delivery details as JSON."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from forward.core.briefing_models import BriefingConfig
from forward.core.briefing_ports import (
    BriefingStoragePort,
)


class JsonBriefingStorageAdapter(BriefingStoragePort):
    """Read and write briefing data under the configured data directory."""

    def __init__(self, data_root: Path) -> None:
        """Create the adapter.

        Args:
            data_root: Configured directory for application data.
        """
        alert_directory = data_root / "alerts"
        self._config_path = alert_directory / "briefing.json"
        self._last_sent_path = alert_directory / "briefing_last.json"

    def load_config(self) -> BriefingConfig:
        """Load saved settings or return the default configuration."""
        if not self._config_path.exists():
            return BriefingConfig()
        payload = json.loads(self._config_path.read_text(encoding="utf8"))
        return BriefingConfig.model_validate(payload)

    def save_config(self, config: BriefingConfig) -> BriefingConfig:
        """Write briefing settings and return them.

        Args:
            config: Validated briefing settings.

        Returns:
            The saved settings.
        """
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        self._config_path.write_text(config.model_dump_json(indent=1), encoding="utf8")
        return config

    def last_sent(self) -> dict[str, Any] | None:
        """Return the last recorded send, if the file has valid JSON."""
        if not self._last_sent_path.exists():
            return None
        return json.loads(self._last_sent_path.read_text(encoding="utf8"))

    def record_sent(self, message: str, channel: str) -> None:
        """Save details about a successful send.

        Args:
            message: Sent briefing text.
            channel: Name of the channel that accepted the message.
        """
        self._last_sent_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "sent_at": datetime.now().isoformat(timespec="seconds"),
            "channel": channel,
            "chars": len(message),
        }
        self._last_sent_path.write_text(json.dumps(payload, indent=1), encoding="utf8")
