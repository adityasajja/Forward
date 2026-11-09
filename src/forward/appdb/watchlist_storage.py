"""SQL adapter for user-owned watchlists."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, NoReturn

from sqlalchemy.exc import IntegrityError

from forward.appdb.audit_storage import record_audit_event
from forward.appdb.engine import AppDatabase
from forward.appdb.watchlist_repository import WatchlistRepository
from forward.audit.events import build_audit_event
from forward.core.audit_ports import AuditLogPort
from forward.core.errors import DuplicateWatchlistName


class SqlWatchlistStorage:
    """Store watchlists and write audit events through configured adapters."""

    def __init__(self, database: AppDatabase, audit_log: AuditLogPort) -> None:
        """Create the adapter for one application database.

        Args:
            database: Database that owns the SQL session lifecycle.
            audit_log: Append-only file audit adapter.
        """
        self._database = database
        self._audit_log = audit_log

    def list_watchlists(self, user_id: str) -> list[dict[str, Any]]:
        """Return a user's watchlists with their configured columns.

        Args:
            user_id: Owner of the requested watchlists.

        Returns:
            Watchlist records ordered as stored for the user.
        """
        with self._database.session() as session:
            rows = WatchlistRepository.list_for_user(session, user_id)
            for row in rows:
                row["columns"] = self._column_keys(session, row["watchlist_id"])
            return rows

    def create(
        self, user_id: str, name: str, exchange: str, columns: Sequence[str]
    ) -> dict[str, Any]:
        """Create a watchlist and return its complete stored record.

        Args:
            user_id: Owner of the new watchlist.
            name: Display name.
            exchange: Default exchange for newly added instruments.
            columns: Ordered set of visible column keys.

        Returns:
            The created watchlist including its items and columns.

        Raises:
            DuplicateWatchlistName: The user already has this name.
        """
        try:
            with self._database.session() as session:
                row = WatchlistRepository.create(
                    session,
                    user_id=user_id,
                    name=name,
                    exchange=exchange,
                    columns=list(columns),
                )
                record_audit_event(
                    session,
                    self._audit_log,
                    action="watchlist.create",
                    user_id=user_id,
                    target_type="watchlist",
                    target_id=row["watchlist_id"],
                    detail={"name": name, "exchange": exchange, "columns": list(columns)},
                )
                return self._details(session, row)
        except IntegrityError as exc:
            self._raise_duplicate_name(exc)

    def get(self, user_id: str, watchlist_id: str) -> dict[str, Any] | None:
        """Return a watchlist only when it belongs to the requested user.

        Args:
            user_id: Owner allowed to read the watchlist.
            watchlist_id: Watchlist to read.

        Returns:
            The watchlist, or ``None`` when it is absent or belongs to another user.
        """
        with self._database.session() as session:
            row = WatchlistRepository.get(session, watchlist_id, user_id)
            return self._details(session, row) if row is not None else None

    def update(
        self, user_id: str, watchlist_id: str, fields: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Update a user's watchlist and return its stored record.

        Args:
            user_id: Owner allowed to update the watchlist.
            watchlist_id: Watchlist to update.
            fields: Editable watchlist fields.

        Returns:
            The updated watchlist, or ``None`` when it is not owned by the user.

        Raises:
            DuplicateWatchlistName: The new name is already in use.
        """
        try:
            with self._database.session() as session:
                changed = WatchlistRepository.update(session, watchlist_id, user_id, **fields)
                if not changed:
                    return None
                record_audit_event(
                    session,
                    self._audit_log,
                    action="watchlist.update",
                    user_id=user_id,
                    target_type="watchlist",
                    target_id=watchlist_id,
                    detail={key: str(value) for key, value in fields.items()},
                )
                row = WatchlistRepository.get(session, watchlist_id, user_id)
                return self._details(session, row) if row is not None else None
        except IntegrityError as exc:
            self._raise_duplicate_name(exc)

    def delete(self, user_id: str, watchlist_id: str) -> bool:
        """Delete a user's watchlist and report whether it existed.

        Args:
            user_id: Owner allowed to delete the watchlist.
            watchlist_id: Watchlist to delete.

        Returns:
            Whether a row was deleted.
        """
        with self._database.session() as session:
            deleted = WatchlistRepository.delete(session, watchlist_id, user_id)
        if deleted:
            self._audit_log.append(
                build_audit_event(
                    action="watchlist.delete",
                    user_id=user_id,
                    target_type="watchlist",
                    target_id=watchlist_id,
                )
            )
        return deleted > 0

    def set_default(self, user_id: str, watchlist_id: str) -> dict[str, Any] | None:
        """Set a user's default watchlist and return its stored record.

        Args:
            user_id: Owner whose default is updated.
            watchlist_id: Watchlist to make the default.

        Returns:
            The updated watchlist, or ``None`` when it is not owned by the user.
        """
        with self._database.session() as session:
            row = WatchlistRepository.get(session, watchlist_id, user_id)
            if row is None:
                return None
            WatchlistRepository.set_default(session, watchlist_id, user_id)
            updated = WatchlistRepository.get(session, watchlist_id, user_id)
            return self._details(session, updated) if updated is not None else None

    def add_items(
        self,
        user_id: str,
        watchlist_id: str,
        symbols: Sequence[str],
        unknown: Sequence[str],
    ) -> tuple[list[str], list[str]] | None:
        """Add symbols and return the added/skipped values.

        Args:
            user_id: Owner allowed to change the watchlist.
            watchlist_id: Watchlist receiving the symbols.
            symbols: Canonical ticker symbols to add.

        Returns:
            Added and skipped symbols, or ``None`` when the watchlist is not owned.
        """
        with self._database.session() as session:
            if WatchlistRepository.get(session, watchlist_id, user_id) is None:
                return None
            added, skipped = WatchlistRepository.add_items(
                session, watchlist_id, list(symbols)
            )
            if added:
                record_audit_event(
                    session,
                    self._audit_log,
                    action="watchlist.items_add",
                    user_id=user_id,
                    target_type="watchlist",
                    target_id=watchlist_id,
                    detail={"added": added, "unknown": list(unknown)},
                )
            return added, skipped

    def remove_item(self, user_id: str, watchlist_id: str, symbol: str) -> bool:
        """Remove one symbol from a watchlist owned by the user.

        Args:
            user_id: Owner allowed to change the watchlist.
            watchlist_id: Watchlist containing the symbol.
            symbol: Ticker to remove.

        Returns:
            Whether a symbol was removed.
        """
        with self._database.session() as session:
            if WatchlistRepository.get(session, watchlist_id, user_id) is None:
                return False
            removed = WatchlistRepository.remove_item(session, watchlist_id, symbol)
            if removed:
                record_audit_event(
                    session,
                    self._audit_log,
                    action="watchlist.items_remove",
                    user_id=user_id,
                    target_type="watchlist",
                    target_id=watchlist_id,
                    detail={"symbol": symbol.upper()},
                )
            return removed > 0

    def reorder(
        self, user_id: str, watchlist_id: str, symbols: Sequence[str]
    ) -> dict[str, Any] | None:
        """Reorder a user's watchlist and return the new record.

        Args:
            user_id: Owner allowed to change the watchlist.
            watchlist_id: Watchlist to reorder.
            symbols: Ticker order supplied by the caller.

        Returns:
            The reordered watchlist with the number of moved items, or ``None``
            when it is not owned by the user.
        """
        with self._database.session() as session:
            if WatchlistRepository.get(session, watchlist_id, user_id) is None:
                return None
            moved = WatchlistRepository.reorder_items(session, watchlist_id, list(symbols))
            row = WatchlistRepository.get(session, watchlist_id, user_id)
            details = self._details(session, row) if row is not None else None
            if details is not None:
                details["moved"] = moved
            return details

    def set_columns(
        self,
        user_id: str,
        watchlist_id: str,
        columns: Sequence[str],
        rejected: Sequence[str],
    ) -> dict[str, Any] | None:
        """Replace a user's watchlist columns and return the new record.

        Args:
            user_id: Owner allowed to change the watchlist.
            watchlist_id: Watchlist to update.
            columns: Ordered column keys to store.

        Returns:
            The updated watchlist, or ``None`` when it is not owned by the user.
        """
        with self._database.session() as session:
            if WatchlistRepository.get(session, watchlist_id, user_id) is None:
                return None
            WatchlistRepository.set_columns(session, watchlist_id, list(columns))
            record_audit_event(
                session,
                self._audit_log,
                action="watchlist.columns_set",
                user_id=user_id,
                target_type="watchlist",
                target_id=watchlist_id,
                detail={"columns": list(columns), "rejected": list(rejected)},
            )
            row = WatchlistRepository.get(session, watchlist_id, user_id)
            return self._details(session, row) if row is not None else None

    @staticmethod
    def _column_keys(session: Any, watchlist_id: str) -> list[str]:
        return [row["key"] for row in WatchlistRepository.columns(session, watchlist_id)]

    @classmethod
    def _details(cls, session: Any, row: dict[str, Any]) -> dict[str, Any]:
        details = dict(row)
        details["items"] = [
            item["symbol"] for item in WatchlistRepository.items(session, row["watchlist_id"])
        ]
        details["columns"] = cls._column_keys(session, row["watchlist_id"])
        return details

    @staticmethod
    def _raise_duplicate_name(error: IntegrityError) -> NoReturn:
        message = str(error.orig).lower()
        if "unique" in message or "duplicate" in message:
            raise DuplicateWatchlistName from error
        raise error
