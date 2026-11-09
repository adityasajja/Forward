"""SQL storage adapter for saved screener definitions."""

from __future__ import annotations

from typing import Any

from forward.appdb.engine import AppDatabase
from forward.appdb.screener_repository import ScreenerRepository
from forward.core.errors import DuplicateScreenerName
from forward.core.screener_ports import ScreenerStoragePort


class SqlScreenerStorageAdapter(ScreenerStoragePort):
    """Store screener rows in the application database."""

    def __init__(self, database: AppDatabase) -> None:
        """Create the adapter.

        Args:
            database: Application database that owns saved screens.
        """
        self._database = database

    def create(
        self,
        user_id: str,
        name: str,
        definition: dict[str, Any],
        description: str | None,
    ) -> dict[str, Any]:
        """Insert one saved screen after checking its name.

        Args:
            user_id: Owner of the screen.
            name: Screen name.
            definition: Validated screen rules and options.
            description: Optional screen description.

        Returns:
            The stored screen row.
        """
        with self._database.session() as session:
            if ScreenerRepository.name_taken(session, user_id, name):
                raise DuplicateScreenerName(name)
            return ScreenerRepository.create(
                session,
                user_id=user_id,
                name=name,
                definition=definition,
                description=description,
            )

    def list_for_user(self, user_id: str) -> list[dict[str, Any]]:
        """Return the user's saved screens.

        Args:
            user_id: Owner of the screens.

        Returns:
            Saved screen rows ordered by name.
        """
        with self._database.session() as session:
            return ScreenerRepository.list_for_user(session, user_id)

    def list_all(self) -> list[dict[str, Any]]:
        """Return all saved screens for the watch scheduler.

        Returns:
            Saved screen rows for every user.
        """
        with self._database.session() as session:
            return ScreenerRepository.list_all(session)

    def get(self, scan_id: str, user_id: str) -> dict[str, Any] | None:
        """Find one screen if it belongs to the requested user.

        Args:
            scan_id: Screen identifier.
            user_id: Required owner of the screen.

        Returns:
            The screen row, or ``None`` when it is missing or has another owner.
        """
        with self._database.session() as session:
            return ScreenerRepository.get(session, scan_id, user_id)

    def delete(self, scan_id: str, user_id: str) -> bool:
        """Delete one screen owned by a user.

        Args:
            scan_id: Screen identifier.
            user_id: Required owner of the screen.

        Returns:
            Whether one row was deleted.
        """
        with self._database.session() as session:
            return bool(ScreenerRepository.delete(session, scan_id, user_id))

    def update(
        self,
        scan_id: str,
        user_id: str,
        *,
        name: str | None,
        description: str | None,
        definition: dict[str, Any] | None,
    ) -> bool:
        """Update one saved screen and check a renamed name in one transaction.

        Args:
            scan_id: Screen identifier.
            user_id: Required owner of the screen.
            name: New name, or ``None`` to keep the current name.
            description: New description, or ``None`` to keep the current one.
            definition: New rules, or ``None`` to keep the current rules.

        Returns:
            Whether one row was updated.
        """
        with self._database.session() as session:
            current = ScreenerRepository.get(session, scan_id, user_id)
            if current is None:
                return False
            renaming = (
                name is not None
                and name.strip().lower() != str(current.get("name") or "").lower()
            )
            if renaming and ScreenerRepository.name_taken(session, user_id, name):
                raise DuplicateScreenerName(name)
            return bool(
                ScreenerRepository.update(
                    session,
                    scan_id,
                    user_id,
                    name=name,
                    description=description,
                    definition=definition,
                )
            )
