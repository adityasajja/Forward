"""Read day-scoped IIFL trade and position rows."""

from __future__ import annotations

from typing import Any

from forward.brokers.iifl.client import IiflClient
from forward.brokers.iifl.responses import broker_error, empty_state, rows
from forward.core.tradebook_ports import TradebookReadPort


def _records(payload: Any) -> list[dict[str, Any]]:
    """Read response rows and raise when IIFL reports an error.

    Args:
        payload: Raw IIFL response envelope.

    Returns:
        Non-empty broker records.

    Raises:
        RuntimeError: If IIFL rejects the request.
    """
    error = broker_error(payload)
    if error:
        raise RuntimeError(error)
    return [row for row in rows(payload) if not empty_state(row)]


class IiflTradebookReadAdapter(TradebookReadPort):
    """Read today's tradebook and positions from one IIFL session."""

    def __init__(self, client: IiflClient) -> None:
        """Create the adapter.

        Args:
            client: Authenticated IIFL client.
        """
        self._client = client

    def trades(self) -> list[dict[str, Any]]:
        """Return raw trade rows from IIFL.

        Returns:
            Broker trade records.
        """
        return _records(self._client.trades())

    def positions(self) -> list[dict[str, Any]]:
        """Return raw position rows from IIFL.

        Returns:
            Broker position records.
        """
        return _records(self._client.positions())
