"""Ports for system state."""

from __future__ import annotations

from typing import Any, Protocol


class SystemStateStoragePort(Protocol):
    """Read and write durable application state by key."""

    def get(self, key: str) -> Any | None:
        """Read the value stored for a key.

        Args:

            key: Stable name for one state value.

        Returns:

            The stored value, or ``None`` when no value exists.
        """

    def set(self, key: str, value: Any, *, actor: str, reason: str) -> None:
        """Write one state value.

        Args:

            key: Stable name for the value.

            value: JSON-serializable state.

            actor: User or system component making the change.

            reason: Human-readable reason recorded with the change.
        """
