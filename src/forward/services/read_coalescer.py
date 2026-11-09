"""Share identical reads while they are in flight."""

from __future__ import annotations

from collections.abc import Callable, Hashable
from concurrent.futures import Future
from copy import deepcopy
from threading import Lock
from typing import TypeVar

T = TypeVar("T")


class ReadCoalescer:
    """Run one copy of each overlapping read and return independent results."""

    def __init__(self) -> None:
        """Create an empty set of in-flight reads."""
        self._lock = Lock()
        self._pending: dict[Hashable, Future[T]] = {}

    def run(self, key: Hashable, read: Callable[[], T]) -> T:
        """Share a read with callers that use the same key at the same time.

        Args:
            key: Identity of the read that can be shared.
            read: Function that performs the read.

        Returns:
            A copy of the result, so callers cannot change one another's data.
        """
        with self._lock:
            future = self._pending.get(key)
            owner = future is None
            if owner:
                future = Future()
                self._pending[key] = future
        assert future is not None
        if owner:
            try:
                future.set_result(read())
            except BaseException as exc:
                future.set_exception(exc)
            finally:
                with self._lock:
                    self._pending.pop(key, None)
        return deepcopy(future.result())
