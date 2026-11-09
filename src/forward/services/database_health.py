"""Application use case for database health checks."""

from __future__ import annotations

from forward.core.market_data_status_ports import DatabaseHealthPort


class DatabaseHealthService:
    """Read database availability through its application port."""

    def __init__(self, health_port: DatabaseHealthPort) -> None:
        """Create the service.

        Args:
            health_port: Adapter that checks database availability.
        """
        self._health_port = health_port

    def is_available(self) -> bool:
        """Return the latest database status without waiting for a probe."""
        return self._health_port.is_available()
