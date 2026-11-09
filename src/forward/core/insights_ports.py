"""Ports for the related application workflows."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Protocol

InsightsHoldingsSource = Literal["LIVE", "SNAPSHOT", "EXPORT"]


class InsightsStateStoragePort(Protocol):
    """Read and write settings and delivery state for daily insights."""

    def load_settings(self) -> Mapping[str, Any] | None:
        """Load saved insight settings.



        Returns:

            Saved settings, or None when no settings file exists.

        """

    def save_settings(self, settings: Mapping[str, Any]) -> None:
        """Write insight settings.



        Args:

            settings: Settings fields that can be encoded as JSON.

        """

    def load_state(self) -> Mapping[str, Any]:
        """Load delivery and comparison state.



        Returns:

            State saved by a previous insights run.

        """

    def save_state(self, state: Mapping[str, Any]) -> None:
        """Write delivery and comparison state.



        Args:

            state: State fields that can be encoded as JSON.

        """


class InsightsHoldingsPort(Protocol):
    """Read current holdings and their source details for an insights report."""

    def load(
        self, *, source: InsightsHoldingsSource, client: Any | None = None
    ) -> dict[str, Any]:
        """Return holdings with their source and last update time.



        Args:

            source: Explicit source to use for this read.

            client: Authenticated broker client when one is available.



        Returns:

            A mapping with ``holdings``, ``source``, and ``as_of`` fields.

        """


class InsightsWatchlistPort(Protocol):
    """Read watchlist symbols for a user's insights report."""

    def symbols_for_user(self, user_id: str) -> set[str]:
        """Return symbols from all watchlists owned by a user.



        Args:

            user_id: Owner whose watchlists should be read.



        Returns:

            Unique trading symbols across the user's watchlists.

        """
