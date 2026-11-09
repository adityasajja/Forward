"""Chart market data use cases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from forward.core.broker_catalog_ports import BrokerInstrumentCatalogPort
from forward.core.chart_market_ports import ChartMarketDataPort
from forward.core.errors import ContractCatalogUnavailable
from forward.core.market_models import InstrumentReference, QuoteBatch


@dataclass(frozen=True, slots=True)
class ResolvedQuoteRequest:
    """Resolved quote instruments and symbols that could not be found.

    Attributes:
        instruments: Listed contracts that can be quoted.
        symbols: Symbols that match the listed contracts in the same order.
        failed: Symbols that could not be resolved and their error messages.
    """

    instruments: list[InstrumentReference]
    symbols: list[str]
    failed: list[dict[str, str]]


class ChartMarketDataService:
    """Resolve listed contracts and request chart market data."""

    def __init__(
        self,
        catalog: BrokerInstrumentCatalogPort,
        market_data: ChartMarketDataPort,
    ) -> None:
        """Create the chart market data service.

        Args:
            catalog: Adapter that resolves exact listed contracts.
            market_data: Adapter that reads candles and quotes.
        """
        self._catalog = catalog
        self._market_data = market_data

    def candles(
        self,
        symbol: str,
        exchange: str,
        interval: str,
        from_date: str,
        to_date: str,
    ) -> list[dict[str, Any]]:
        """Resolve a listed contract and load its candles.

        Args:
            symbol: Exact trading symbol.
            exchange: Exchange segment containing the symbol.
            interval: Requested candle interval.
            from_date: First date in IIFL's date format.
            to_date: Last date in IIFL's date format.

        Returns:
            Chart-ready OHLCV rows.
        """
        normalized_exchange = exchange.upper()
        instrument = InstrumentReference(
            symbol=symbol.upper(),
            exchange=normalized_exchange,
            contract_id=self._catalog.contract_id(symbol, normalized_exchange),
        )
        return self._market_data.historical_candles(
            instrument, interval, from_date, to_date
        )

    def resolve_quotes(
        self, symbols: list[str], exchange: str
    ) -> ResolvedQuoteRequest:
        """Resolve quote symbols and collect per-symbol lookup errors.

        Args:
            symbols: Symbols requested by the caller.
            exchange: Exchange segment containing the symbols.

        Returns:
            Listed contracts, their original symbols, and lookup errors.
        """
        normalized_exchange = exchange.upper()
        instruments = []
        resolved_symbols = []
        failed = []
        for symbol in symbols:
            try:
                instruments.append(
                    InstrumentReference(
                        symbol=symbol,
                        exchange=normalized_exchange,
                        contract_id=self._catalog.contract_id(
                            symbol, normalized_exchange
                        ),
                    )
                )
                resolved_symbols.append(symbol)
            except ContractCatalogUnavailable:
                raise
            except Exception as exc:  # noqa: BLE001 - keep lookup failures per symbol
                failed.append({"symbol": symbol, "error": str(exc)[:160]})
        return ResolvedQuoteRequest(instruments, resolved_symbols, failed)

    def quote_payload(self, instruments: list[InstrumentReference]) -> QuoteBatch:
        """Request current quotes for already-resolved instruments.

        Args:
            instruments: Listed instruments to quote.

        Returns:
            Normalized quote rows and any broker error text.
        """
        return self._market_data.quote_payload(instruments)
