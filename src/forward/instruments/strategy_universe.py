"""Provide the research universe with exact broker symbols."""

from __future__ import annotations

from forward.core.broker_catalog_ports import BrokerInstrumentCatalogPort
from forward.core.historical_market_data_ports import CachedMarketFramesPort
from forward.core.instrument_ports import (
    StrategyUniversePort,
)
from forward.research.hunt import STOCK_EXCHANGE, stock_universe

EXCHANGE = STOCK_EXCHANGE
MINIMUM_HISTORY_BARS = 1500


class ResearchedStrategyUniverse(StrategyUniversePort):
    """Read researched stocks and confirm their exact broker contract symbols."""

    def __init__(
        self,
        history: CachedMarketFramesPort,
        contracts: BrokerInstrumentCatalogPort,
    ) -> None:
        """Create a universe adapter with its market-history dependency.

        Args:
            history: Port that reads cached daily frames.
            contracts: Port that confirms exact listed contract symbols.
        """
        self._history = history
        self._contracts = contracts

    def researched_universe(self) -> list[str]:
        """Return researched stocks with exact NSE equity contract symbols.

        Returns:
            Symbols with enough cached history and an exact listed contract.

        Raises:
            KeyError: If a research symbol has no exact NSE equity contract.
        """
        symbols = stock_universe(
            self._history, min_bars=MINIMUM_HISTORY_BARS
        )
        return [self._exact_contract_symbol(symbol) for symbol in symbols]

    def _exact_contract_symbol(self, symbol: str) -> str:
        """Return the exact contract spelling for a researched stock.

        Args:
            symbol: Stock symbol from the research universe.

        Returns:
            Exact symbol in the NSE equity segment.

        Raises:
            KeyError: If the exact contract is missing.
        """
        contract_symbol = symbol if symbol.endswith("-EQ") else f"{symbol}-EQ"
        return self._contracts.listed_symbol(contract_symbol, EXCHANGE)


__all__ = ["ResearchedStrategyUniverse"]
