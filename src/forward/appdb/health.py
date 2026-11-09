"""Database health adapter with a non-blocking cached probe."""

from __future__ import annotations

import threading
import time

from forward.appdb.engine import AppDatabase


class AppDatabaseHealthAdapter:
    """Check database availability outside the request thread."""

    def __init__(self, database: AppDatabase, cache_ttl_seconds: float) -> None:
        """Create the health adapter.

        Args:
            database: Database connection owner to probe.
            cache_ttl_seconds: Time to keep the last probe result.
        """
        self._database = database
        self._cache_ttl_seconds = cache_ttl_seconds
        self._checked_at = 0.0
        self._available = False
        self._probing = False
        self._lock = threading.Lock()

    def is_available(self) -> bool:
        """Return the cached result and start a probe when it expires."""
        now = time.monotonic()
        with self._lock:
            if now - self._checked_at >= self._cache_ttl_seconds and not self._probing:
                self._probing = True
                threading.Thread(
                    target=self._probe,
                    daemon=True,
                    name="forward-db-probe",
                ).start()
            return self._available

    def _probe(self) -> None:
        """Refresh the cached database status in the background."""
        try:
            available = self._database.health()
        except Exception:
            with self._lock:
                self._probing = False
            raise
        with self._lock:
            self._available = available
            self._checked_at = time.monotonic()
            self._probing = False
