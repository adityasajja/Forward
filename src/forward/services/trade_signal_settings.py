"""Read and update the settings used to build trade signals."""

from __future__ import annotations

from forward.core.trade_signal_models import TradeSignalSettings
from forward.core.trade_signal_ports import TradeSignalStoragePort


class TradeSignalSettingsService:
    """Read and update trade signal settings through a storage port."""

    def __init__(self, storage: TradeSignalStoragePort) -> None:
        """Create the settings service.

        Args:
            storage: Adapter for saved trade signal settings.
        """
        self._storage = storage

    def load(self) -> TradeSignalSettings:
        """Return saved settings or the model defaults on first use.

        Returns:
            Validated trade signal settings.
        """
        values = self._storage.load_settings()
        if values is None:
            return TradeSignalSettings()
        return TradeSignalSettings.model_validate(values)

    def save(self, settings: TradeSignalSettings) -> TradeSignalSettings:
        """Persist validated settings.

        Args:
            settings: Settings to save.

        Returns:
            The saved settings.
        """
        self._storage.save_settings(settings.model_dump(mode="json"))
        return settings

    def update(self, changes: dict[str, object]) -> TradeSignalSettings:
        """Merge editor changes into the current settings and save them.

        Args:
            changes: Settings fields supplied by the editor.

        Returns:
            The validated settings after the update.
        """
        values = {**self.load().model_dump(), **changes}
        return self.save(TradeSignalSettings.model_validate(values))
