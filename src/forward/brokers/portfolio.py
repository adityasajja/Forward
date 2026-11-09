"""Broker-backed portfolio view used by live risk checks."""

from __future__ import annotations

import logging
from typing import Any

from forward.brokers.base import Broker

logger = logging.getLogger("forward.brokers.portfolio")


class BrokerPortfolio:
    """A portfolio view over a broker's own positions, for the risk engine.

    ``RiskEngine`` needs ``position(symbol)`` and the aggregates. In live trading
    the broker *is* the source of truth for positions, so this reads them rather
    than keeping a second copy in the platform — two position books that can
    disagree is precisely what reconciliation exists to detect, and creating one
    on purpose would be a poor way to start.

    **``equity`` is reported as 0.0 and that is a real limitation, not a value.**
    ``IiflBroker`` exposes positions and per-order margin but no funds endpoint, so
    the platform does not know the account's equity. A ``max_daily_loss`` limit is
    measured from equity, so on the live path that one limit cannot fire. It is
    reported as zero rather than as a guess because a guess here would silently
    switch a safety limit on or off. Adding ``funds()`` to the ``Broker`` ABC is
    tracked in the gap report; until then this class says so out loud.
    """

    def __init__(self, broker: Broker) -> None:
        """Create a portfolio view over one authenticated broker.

        Args:
            broker: Broker that provides current positions.
        """
        self._broker = broker
        self._by_symbol: dict[str, Any] = {}
        self._loaded = False
        self.readable = True

    def _load(self) -> None:
        if self._loaded:
            return
        try:
            for position in self._broker.positions():
                self._by_symbol[position.instrument.symbol] = position
        except Exception:  # noqa: BLE001 - an unreadable book is "no positions known"
            logger.exception("could not read positions from the broker")
            self.readable = False
        self._loaded = True

    def day_pnl(self) -> float | None:
        """Today's P&L (realised + open) from the broker's own day positions, or ``None`` if
        the book could not be read — unknown is not zero."""
        self._load()
        if not self.readable:
            return None
        return sum(
            float(p.realized_pnl) + float(p.unrealized_pnl) for p in self._by_symbol.values()
        )

    def position(self, symbol: str) -> Any:
        """Return the broker position for one symbol.

        Args:
            symbol: Instrument symbol to find.

        Returns:
            The broker position or a flat position when none exists.
        """
        self._load()
        found = self._by_symbol.get(symbol)
        if found is not None:
            return found
        return _FlatPosition()

    @property
    def positions(self) -> dict[str, Any]:
        self._load()
        return dict(self._by_symbol)

    @property
    def gross_exposure(self) -> float:
        self._load()
        return sum(abs(p.market_value) for p in self._by_symbol.values())

    @property
    def equity(self) -> float:
        return 0.0


class _FlatPosition:
    """No position in this symbol. The shape ``RiskEngine`` expects."""

    quantity = 0.0
    avg_price = 0.0
    last_price = 0.0

    @property
    def is_flat(self) -> bool:
        return True
