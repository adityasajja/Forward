"""IIFL quote adapter for price and indicator alerts."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime

from forward.brokers.iifl.client import IiflClient
from forward.brokers.iifl.contracts import InstrumentMaster
from forward.brokers.iifl.responses import broker_error, rows
from forward.core.alert_ports import AlertQuote
from forward.core.market_models import Bar


def _required_price(row: dict, field: str) -> float:
    """Read one finite positive quote price.

    Args:
        row: IIFL quote row.
        field: Field name reported in errors.

    Returns:
        A positive price in rupees.

    Raises:
        ValueError: If the field is missing, invalid, or non-positive.
    """
    raw = row.get(field)
    if raw is None:
        raise ValueError(f"IIFL quote has no {field}")
    value = float(raw)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"IIFL quote has an invalid {field}: {raw!r}")
    return value


def _quote_timestamp(row: dict) -> datetime:
    """Parse the exact timestamp field documented by IIFL's quote endpoint."""
    raw = row.get("tickTimestamp")
    if not isinstance(raw, str) or not raw:
        raise ValueError("IIFL quote has no tickTimestamp")
    try:
        return datetime.strptime(raw, "%d-%b-%Y %H:%M:%S")
    except ValueError as exc:
        raise ValueError(f"IIFL quote has an invalid tickTimestamp: {raw!r}") from exc


def _quote_bar(row: dict) -> Bar:
    """Build a bar from the observed IIFL quote fields without filling gaps."""
    open_price = _required_price(row, "open")
    high = _required_price(row, "high")
    low = _required_price(row, "low")
    close = _required_price(row, "ltp")
    if low > min(open_price, close) or high < max(open_price, close) or low > high:
        raise ValueError("IIFL quote OHLC values are inconsistent")
    raw_volume = row.get("tradedVolume")
    volume = None if raw_volume is None else float(raw_volume)
    if volume is not None and (not math.isfinite(volume) or volume < 0):
        raise ValueError(f"IIFL quote has invalid tradedVolume: {raw_volume!r}")
    return Bar(
        ts=_quote_timestamp(row),
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


class IiflAlertQuoteAdapter:
    """Read alert quotes through the authenticated IIFL market-data client."""

    def __init__(self, client: IiflClient) -> None:
        """Create the adapter for an authenticated client.

        Args:
            client: Authenticated client used to request market quotes.
        """
        self._client = client

    def quotes(
        self, symbols: Sequence[tuple[str, str]]
    ) -> dict[str, AlertQuote]:
        """Fetch quotes keyed by the requested symbol.

        Args:
            symbols: Symbol and exchange pairs requested by armed alert rules.

        Returns:
            The market quote fields keyed by symbol.
        """
        requested: dict[str, tuple[str, str]] = {}
        for symbol, exchange in symbols:
            key = (symbol, exchange.upper())
            existing = requested.get(symbol)
            if existing is not None and existing != key:
                raise ValueError(f"alert symbol {symbol} was requested on multiple exchanges")
            requested[symbol] = key
        if not requested:
            return {}

        master = InstrumentMaster(self._client)
        master.load_cached(sorted({exchange for _, exchange in requested.values()}))
        symbols_by_key: dict[tuple[str, str], str] = {}
        for symbol, (raw_symbol, exchange) in requested.items():
            contract = master.find(raw_symbol, exchange)
            if contract.conid is None:
                raise ValueError(f"IIFL contract {raw_symbol} on {exchange} has no instrument ID")
            symbols_by_key[(exchange, str(contract.conid))] = symbol

        response = self._client.market_quotes(list(symbols_by_key))
        error = broker_error(response)
        if error:
            raise RuntimeError(error)
        quote_rows = rows(response)
        quotes: dict[str, AlertQuote] = {}
        for index, row in enumerate(quote_rows):
            exchange = row.get("exchange")
            instrument_id = row.get("instrumentId")
            if exchange is None or instrument_id is None:
                raise ValueError(f"IIFL quote row {index} has no contract identity")
            key = (str(exchange).upper(), str(instrument_id))
            symbol = symbols_by_key.get(key)
            if symbol is None:
                raise ValueError(f"IIFL returned an unrequested quote contract: {key}")
            last = _required_price(row, "ltp")
            previous_close = _required_price(row, "close")
            bar = _quote_bar(row)
            quotes[symbol] = {
                "ltp": last,
                "prev_close": previous_close,
                "day_chg": (last / previous_close - 1) * 100,
                "bar": bar,
            }
        missing = set(symbols_by_key.values()) - quotes.keys()
        if missing:
            raise RuntimeError(f"IIFL did not return quotes for: {sorted(missing)}")
        return quotes
