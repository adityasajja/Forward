"""Store intelligent alert settings as JSON."""

from __future__ import annotations

import json

from forward.config.settings import research_data_root
from forward.core.alert_config import IntelligentAlertConfig
from forward.core.alert_ports import (
    IntelligentAlertConfigPort,
)


class JsonIntelligentAlertConfigAdapter(IntelligentAlertConfigPort):
    """Read and write alert settings in the configured data directory."""

    def __init__(self) -> None:
        """Create the adapter using Forward's configured data directory."""
        self._path = research_data_root() / "alerts" / "intelligent.json"

    def load(self) -> IntelligentAlertConfig:
        """Load saved settings or create the initial configuration.

        Returns:
            The stored settings, or the initial configuration when no file exists.
        """
        if self._path.exists():
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            return IntelligentAlertConfig.model_validate(payload)
        return IntelligentAlertConfig()

    def save(self, config: IntelligentAlertConfig) -> IntelligentAlertConfig:
        """Write settings and return the saved value.

        Args:
            config: Settings to persist.

        Returns:
            The same settings after the write succeeds.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(config.model_dump_json(indent=2), encoding="utf-8")
        return config
