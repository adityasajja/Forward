"""Build entry and exit signals from market data and saved thresholds."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from forward.core.instrument_models import Instrument
from forward.core.market_models import MarketQuote
from forward.core.portfolio_models import Holding
from forward.core.signal_ports import (
    SignalMarketPort,
)
from forward.signals.models import Signal, SignalConfig
from forward.signals.rules import append_live_bar, eval_entry, eval_exit, primary_exit


class SignalScanningService:
    """Evaluate saved rules against current holdings and market data."""

    def __init__(
        self,
        market: SignalMarketPort,
        *,
        lookback_days: int,
        max_candidates: int,
    ) -> None:
        """Create the scanner.

        Args:
            market: Port that supplies normalized market data.
            lookback_days: Daily history window requested for each symbol.
            max_candidates: Maximum symbols accepted in one entry scan.
        """
        self._market = market
        self._lookback_days = lookback_days
        self._max_candidates = max_candidates

    def scan_holdings(self, config: SignalConfig) -> tuple[list[Signal], list[str]]:
        """Run exit rules over the account's settled holdings.

        Args:
            config: Entry and exit thresholds for this scan.

        Returns:
            Exit signals and data-quality errors.
        """
        holdings, failure = self._market.holdings(config.exchange)
        if failure is not None:
            return [], [f"sell scan unavailable — {failure}"]
        if not holdings:
            return [], ["no holdings returned"]

        positions = _combine_holdings(holdings)
        quotes = self._market.quotes([position["instrument"] for position in positions.values()])
        signals: list[Signal] = []
        errors: list[str] = []
        for symbol, position in positions.items():
            instrument = position.instrument
            quote = _quote_for(quotes, instrument)
            if quote is None or quote.last <= 0:
                errors.append(f"{symbol}: no quote")
                continue
            frame = self._market.daily_bars(instrument, self._lookback_days)
            if frame.empty:
                errors.append(f"{symbol}: no daily history")
                continue
            frame = append_live_bar(frame, quote.last, quote.volume)
            fired = eval_exit(
                symbol,
                frame,
                position.average_price,
                config.exits,
                quantity=position.quantity,
            )
            best = primary_exit(fired)
            if best is not None:
                signals.append(best)
        return signals, errors

    def scan_universe(
        self, symbols: Sequence[str], config: SignalConfig
    ) -> tuple[list[Signal], list[str]]:
        """Run entry rules over an explicit watchlist.

        Args:
            symbols: Trading symbols to scan.
            config: Entry and exit thresholds for this scan.

        Returns:
            Entry signals and data-quality errors.
        """
        requested = [symbol.strip().upper() for symbol in symbols if symbol.strip()]
        if not requested:
            return [], ["no symbols given"]
        if len(requested) > self._max_candidates:
            raise ValueError(
                f"scan has {len(requested)} symbols; limit is {self._max_candidates}"
            )

        resolved = self._market.resolve(requested, config.exchange)
        missing = [symbol for symbol in requested if symbol not in resolved]
        errors = [f"{symbol}: not in the instrument master" for symbol in missing]
        quotes = self._market.quotes(list(resolved.values()))
        signals: list[Signal] = []
        for symbol, instrument in resolved.items():
            quote = _quote_for(quotes, instrument)
            if quote is None or quote.last <= 0:
                errors.append(f"{symbol}: no quote")
                continue
            frame = self._market.daily_bars(instrument, self._lookback_days)
            if frame.empty:
                errors.append(f"{symbol}: no daily history")
                continue
            frame = append_live_bar(frame, quote.last, quote.volume)
            signals.extend(eval_entry(symbol, frame, config.entries))
        return signals, errors


def _combine_holdings(
    holdings: Sequence[Holding],
) -> dict[str, _CombinedHolding]:
    """Combine separate lots into one weighted position per symbol.

    Args:
        holdings: Broker holdings to combine.

    Returns:
        One weighted position per trading symbol.
    """
    positions: dict[str, _CombinedHolding] = {}
    for holding in holdings:
        symbol = holding.instrument.symbol
        position = positions.setdefault(symbol, _CombinedHolding(holding.instrument))
        position.add(holding)
    return positions


@dataclass
class _CombinedHolding:
    """Weighted account position used while evaluating exit rules."""

    instrument: Instrument
    quantity: float = 0.0
    cost: float = 0.0

    @property
    def average_price(self) -> float:
        """Return the weighted entry price.

        Returns:
            Cost divided by the combined quantity.
        """
        return self.cost / self.quantity if self.quantity else 0.0

    def add(self, holding: Holding) -> None:
        """Include one broker holding row.

        Args:
            holding: Holding row to combine with this position.
        """
        self.quantity += holding.quantity
        self.cost += holding.quantity * holding.avg_price
        if self.instrument.conid is None and holding.instrument.conid is not None:
            self.instrument = holding.instrument


def _quote_for(
    quotes: Mapping[tuple[str, str], MarketQuote],
    instrument: Instrument,
) -> MarketQuote | None:
    """Find a quote by the resolved exchange and instrument id.

    Args:
        quotes: Quotes returned by the market port.
        instrument: Contract whose quote is requested.

    Returns:
        Matching quote, or ``None`` when the contract has no quote.
    """
    if instrument.conid is None:
        return None
    return quotes.get((instrument.exchange.upper(), str(instrument.conid)))
