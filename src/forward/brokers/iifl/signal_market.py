"""IIFL market adapter used by the signal scanning service."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

import pandas as pd

from forward.brokers.iifl.client import IiflClient
from forward.brokers.iifl.contracts import InstrumentMaster
from forward.brokers.iifl.daily_history import load_daily
from forward.brokers.iifl.responses import broker_error, rows
from forward.core.instrument_models import Instrument
from forward.core.market_models import MarketQuote
from forward.core.portfolio_models import Holding

NO_HOLDINGS = "EC920"


class IiflSignalMarketAdapter:
    """Read the broker data needed by buy and sell signal scans."""

    def __init__(self, client: IiflClient) -> None:
        """Create the adapter.

        Args:
            client: Authenticated IIFL client for this scan.
        """
        self._client = client
        self._masters: dict[str, InstrumentMaster] = {}

    def holdings(self, exchange: str) -> tuple[list[Holding], str | None]:
        """Return settled holdings, preserving a broker read error.

        Args:
            exchange: Exchange segment used for holding instruments.

        Returns:
            Normalized holdings and an error message when the read failed.
        """
        payload = self._client.holdings()
        failure = broker_error(payload)
        if failure is not None:
            return [], failure

        holdings = []
        for row in rows(payload):
            symbol = str(row.get("nseTradingSymbol") or row.get("bseTradingSymbol") or "").strip()
            quantity_value = row.get("totalQuantity")
            if quantity_value is None:
                return [], "IIFL holding row has no totalQuantity"
            quantity = float(quantity_value)
            if not symbol or quantity <= 0:
                continue
            raw_id = row.get("nseInstrumentId")
            if exchange.upper() == "BSEEQ":
                raw_id = row.get("bseInstrumentId")
            if raw_id is None:
                return [], f"IIFL holding {symbol} has no {exchange} instrument ID"
            average_price = row.get("averageTradedPrice")
            if average_price is None:
                return [], f"IIFL holding {symbol} has no averageTradedPrice"
            instrument = Instrument(
                symbol=symbol,
                exchange=exchange.upper(),
                currency="INR",
                conid=int(raw_id),
            )
            holdings.append(
                Holding(
                    instrument=instrument,
                    quantity=quantity,
                    avg_price=float(average_price),
                    raw=row,
                )
            )
        return holdings, None

    def resolve(self, symbols: Sequence[str], exchange: str) -> Mapping[str, Instrument]:
        """Resolve symbols against the exact IIFL contract file.

        Args:
            symbols: Trading symbols to resolve.
            exchange: Exchange segment containing the contracts.

        Returns:
            Resolved instruments keyed by the requested symbol.
        """
        master = self._master(exchange)
        resolved = {}
        for symbol in symbols:
            try:
                resolved[symbol] = master.find(symbol, exchange)
            except KeyError:
                continue
        return resolved

    def quotes(self, instruments: Sequence[Instrument]) -> Mapping[tuple[str, str], MarketQuote]:
        """Read one bulk quote response for resolved instruments.

        Args:
            instruments: Instruments to quote.

        Returns:
            Quotes keyed by exchange and instrument id.
        """
        missing_ids = [instrument.symbol for instrument in instruments if instrument.conid is None]
        if missing_ids:
            raise ValueError(f"IIFL quote instruments have no IDs: {missing_ids}")
        by_key = {
            (instrument.exchange.upper(), str(instrument.conid)): instrument
            for instrument in instruments
        }
        legs = list(by_key)
        if not legs:
            return {}

        quotes = {}
        payload = self._client.market_quotes(legs)
        failure = broker_error(payload)
        if failure:
            raise RuntimeError(f"IIFL market quote read failed: {failure}")
        for row in rows(payload):
            key = (str(row.get("exchange", "")).upper(), str(row.get("instrumentId", "")))
            instrument = by_key.get(key)
            if instrument is None:
                continue
            raw_last = row.get("ltp")
            if raw_last is None:
                raise ValueError(f"IIFL quote for {instrument.symbol} has no ltp")
            last = float(raw_last)
            if not math.isfinite(last) or last <= 0:
                raise ValueError(f"IIFL quote for {instrument.symbol} has invalid ltp")
            raw_volume = row.get("tradedVolume")
            volume = None if raw_volume is None else float(raw_volume)
            if volume is not None and (not math.isfinite(volume) or volume < 0):
                raise ValueError(f"IIFL quote for {instrument.symbol} has invalid tradedVolume")
            quotes[key] = MarketQuote(
                instrument=instrument,
                last=last,
                volume=volume,
            )
        return quotes

    def daily_bars(self, instrument: Instrument, lookback_days: int) -> pd.DataFrame:
        """Return daily history for one resolved instrument.

        Args:
            instrument: Instrument whose bars are requested.
            lookback_days: Calendar days requested from IIFL.

        Returns:
            Daily OHLCV bars from the history adapter.
        """
        return load_daily(self._client, instrument, lookback_days)

    def _master(self, exchange: str) -> InstrumentMaster:
        """Return the cached contract index for one exchange.

        Args:
            exchange: Exchange segment whose contract file is needed.

        Returns:
            Loaded contract index.
        """
        normalized_exchange = exchange.upper()
        master = self._masters.get(normalized_exchange)
        if master is None:
            master = InstrumentMaster(self._client)
            master.load_cached([normalized_exchange])
            self._masters[normalized_exchange] = master
        return master
