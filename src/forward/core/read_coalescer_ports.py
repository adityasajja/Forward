"""Port for sharing overlapping broker reads."""

from __future__ import annotations

from collections.abc import Callable, Hashable
from typing import Any, Protocol


class ReadCoalescerPort(Protocol):
    """Share matching reads while they are already running."""

    def run(self, key: Hashable, read: Callable[[], Any]) -> Any:
        """Run one read and share its result with matching calls.

        Args:
            key: Identity used to match concurrent reads.
            read: Function that performs the read.

        Returns:
            A copy of the read result.
        """
