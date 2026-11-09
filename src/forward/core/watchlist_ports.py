"""Ports for the related application workflows."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol


class WatchlistStoragePort(Protocol):
    """Store user-owned watchlists and their configuration."""

    def list_watchlists(self, user_id: str) -> list[dict[str, Any]]:
        """Return a user's watchlists with their configured columns.



        Args:

            user_id: Owner of the requested watchlists.



        Returns:

            Watchlist records in their stored order.

        """

    def create(
        self, user_id: str, name: str, exchange: str, columns: Sequence[str]
    ) -> dict[str, Any]:
        """Create a watchlist and return its complete stored record.



        Args:

            user_id: Owner of the new watchlist.

            name: Display name.

            exchange: Default exchange for new instruments.

            columns: Ordered column keys.



        Returns:

            The created watchlist.

        """

    def get(self, user_id: str, watchlist_id: str) -> dict[str, Any] | None:
        """Return a watchlist only when it belongs to the requested user.



        Args:

            user_id: Owner allowed to read the watchlist.

            watchlist_id: Watchlist to read.



        Returns:

            The watchlist, or ``None`` when it is absent or not owned.

        """

    def update(
        self, user_id: str, watchlist_id: str, fields: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Update a watchlist owned by the user and return its stored record.



        Args:

            user_id: Owner allowed to update the watchlist.

            watchlist_id: Watchlist to update.

            fields: Editable watchlist fields.



        Returns:

            The updated watchlist, or ``None`` when it is absent or not owned.

        """

    def delete(self, user_id: str, watchlist_id: str) -> bool:
        """Delete a user's watchlist and report whether it existed.



        Args:

            user_id: Owner allowed to delete the watchlist.

            watchlist_id: Watchlist to delete.



        Returns:

            Whether the watchlist was deleted.

        """

    def set_default(self, user_id: str, watchlist_id: str) -> dict[str, Any] | None:
        """Set a user's default watchlist and return its stored record.



        Args:

            user_id: Owner whose default is updated.

            watchlist_id: Watchlist to make the default.



        Returns:

            The updated watchlist, or ``None`` when it is absent or not owned.

        """

    def add_items(
        self, user_id: str, watchlist_id: str, symbols: Sequence[str], unknown: Sequence[str]
    ) -> tuple[list[str], list[str]] | None:
        """Add symbols and return added/skipped values.



        Args:

            user_id: Owner allowed to change the watchlist.

            watchlist_id: Watchlist receiving the symbols.

            symbols: Canonical tickers to add.

            unknown: User-supplied symbols that could not be resolved.



        Returns:

            Added and skipped tickers, or ``None`` when the watchlist is not owned.

        """

    def remove_item(self, user_id: str, watchlist_id: str, symbol: str) -> bool:
        """Remove one symbol from a watchlist owned by the user.



        Args:

            user_id: Owner allowed to change the watchlist.

            watchlist_id: Watchlist containing the symbol.

            symbol: Ticker to remove.



        Returns:

            Whether a symbol was removed.

        """

    def reorder(
        self, user_id: str, watchlist_id: str, symbols: Sequence[str]
    ) -> dict[str, Any] | None:
        """Reorder a user's watchlist and return the new record.



        Args:

            user_id: Owner allowed to change the watchlist.

            watchlist_id: Watchlist to reorder.

            symbols: Ticker order supplied by the caller.



        Returns:

            The reordered watchlist, or ``None`` when it is not owned.

        Raises:

            InvalidWatchlistOrder: When the input does not contain each current
                symbol exactly once.

        """

    def set_columns(
        self, user_id: str, watchlist_id: str, columns: Sequence[str], rejected: Sequence[str]
    ) -> dict[str, Any] | None:
        """Replace a user's watchlist columns and return the new record.



        Args:

            user_id: Owner allowed to change the watchlist.

            watchlist_id: Watchlist to update.

            columns: Ordered column keys to store.

            rejected: Requested keys the service did not accept.



        Returns:

            The updated watchlist, or ``None`` when it is not owned.

        """
