"""Storage ports for saved screener definitions."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol


class ScreenerStoragePort(Protocol):
    """Store and retrieve saved screener definitions."""

    def create(
        self,
        user_id: str,
        name: str,
        definition: dict[str, Any],
        description: str | None,
    ) -> dict[str, Any]:
        """Save one new screen for a user.

        Args:
            user_id: Owner of the screen.
            name: Screen name.
            definition: Validated screen rules and options.
            description: Optional screen description.

        Returns:
            The stored screen row.
        """

    def list_for_user(self, user_id: str) -> list[dict[str, Any]]:
        """Return every saved screen owned by a user.

        Args:
            user_id: Owner of the screens.

        Returns:
            Saved screen rows ordered by name.
        """

    def list_all(self) -> list[dict[str, Any]]:
        """Return all saved screens for the watch scheduler.

        Returns:
            Saved screen rows for every user.
        """

    def get(self, scan_id: str, user_id: str) -> dict[str, Any] | None:
        """Find one screen if it belongs to the requested user.

        Args:
            scan_id: Screen identifier.
            user_id: Required owner of the screen.

        Returns:
            The screen row, or ``None`` when it is missing or has another owner.
        """

    def delete(self, scan_id: str, user_id: str) -> bool:
        """Delete one screen owned by a user.

        Args:
            scan_id: Screen identifier.
            user_id: Required owner of the screen.

        Returns:
            Whether one row was deleted.
        """

    def update(
        self,
        scan_id: str,
        user_id: str,
        *,
        name: str | None,
        description: str | None,
        definition: dict[str, Any] | None,
    ) -> bool:
        """Update one screen owned by a user.

        Args:
            scan_id: Screen identifier.
            user_id: Required owner of the screen.
            name: New name, or ``None`` to keep the current name.
            description: New description, or ``None`` to keep the current one.
            definition: New rules, or ``None`` to keep the current rules.

        Returns:
            Whether one row was updated.
        """


class SavedScreenerPort(Protocol):
    """Read and run saved screens for the watch workflow."""

    def get_saved(self, user_id: str, scan_id: str) -> dict[str, Any]:
        """Read one saved screen owned by the user.

        Args:
            user_id: Owner of the screen.
            scan_id: Screen identifier.

        Returns:
            The saved screen and its definition.
        """

    def update_saved(
        self,
        user_id: str,
        scan_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        definition: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Update one saved screen owned by the user.

        Args:
            user_id: Owner of the screen.
            scan_id: Screen identifier.
            name: Replacement name, or ``None`` to keep it.
            description: Replacement description, or ``None`` to keep it.
            definition: Replacement rules, or ``None`` to keep them.

        Returns:
            The updated saved screen.
        """

    def run_saved(self, user_id: str, scan_id: str, **overrides: Any) -> dict[str, Any]:
        """Run one saved screen with optional request overrides.

        Args:
            user_id: Owner of the screen.
            scan_id: Screen identifier.
            **overrides: Values that replace saved scan options for this run.

        Returns:
            The scan result and saved-screen details.
        """

    def list_all(self) -> list[dict[str, Any]]:
        """List saved screens for the scheduler.

        Returns:
            Saved screens with their owner IDs.
        """


class ScreenerWatchNotificationsPort(Protocol):
    """Send watch notifications through the configured Telegram channel."""

    @property
    def configured(self) -> bool:
        """Return whether Telegram has the settings needed to send messages."""

    def send(self, title: str, body: str) -> bool:
        """Send one notification and report whether the channel accepted it.

        Args:
            title: Notification title.
            body: Notification text.

        Returns:
            Whether the channel accepted the notification.
        """


class ScreenerMarketDataPort(Protocol):
    """Resolve screen universes and load their daily market frames."""

    def universes(self, exchange: str) -> list[dict[str, Any]]:
        """Return named universes and their available history counts.

        Args:
            exchange: Exchange segment to report.

        Returns:
            Universe names, labels, member counts, and loadable counts.
        """

    def symbols_for(self, universe: str, exchange: str) -> list[str]:
        """Resolve a universe to symbols with daily history.

        Args:
            universe: Named universe or ``all``.
            exchange: Exchange segment to scan.

        Returns:
            Symbols that have cached daily bars.
        """

    def frames(
        self,
        exchange: str,
        symbols: Sequence[str] | None = None,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        """Load daily frames keyed by canonical symbol.

        Args:
            exchange: Exchange segment to read.
            symbols: Optional exact symbols to read.
            force: Reload the complete exchange cache when true.

        Returns:
            Available daily market frames.
        """
