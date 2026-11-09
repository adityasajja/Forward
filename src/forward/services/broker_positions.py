"""Read and shape normalized broker positions."""

from __future__ import annotations

from typing import Any

from forward.core.broker_account_ports import BrokerPositionReadPort


class BrokerPositionsService:
    """Return broker positions as stable API-ready records."""

    def __init__(self, broker: BrokerPositionReadPort) -> None:
        """Create the position read service.

        Args:
            broker: Port that returns normalized broker positions.
        """
        self._broker = broker

    def read(self) -> list[dict[str, Any]]:
        """Read and shape positions for the operator.

        Returns:
            Positions with symbol, quantity, prices, and P&L fields.
        """
        return [
            {
                "symbol": position.instrument.symbol,
                "exchange": position.instrument.exchange,
                "quantity": position.quantity,
                "avg_price": position.avg_price,
                "last_price": position.last_price,
                "unrealized_pnl": position.unrealized_pnl,
                "realized_pnl": position.realized_pnl,
            }
            for position in self._broker.positions()
        ]
