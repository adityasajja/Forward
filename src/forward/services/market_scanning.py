"""Coordinate market data reads and momentum scoring for scanner requests."""

from __future__ import annotations

from datetime import date
from typing import Any

from forward.core.scanner_market_ports import ScannerMarketPort
from forward.scanner import score_frame


class MarketScanningService:
    """Score a requested universe using a market data port."""

    def __init__(
        self,
        market: ScannerMarketPort,
        *,
        default_symbols: list[str],
        from_date: str,
    ) -> None:
        """Create the scanner service.

        Args:
            market: Port that reads daily bars for listed instruments.
            default_symbols: Symbols included when a request omits its universe.
            from_date: First history date in broker format.
        """
        self._market = market
        self._default_symbols = default_symbols
        self._from_date = from_date

    def scan(
        self,
        symbols: list[str] | None,
        *,
        exchange: str,
        to_date: str | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        """Score each requested instrument and collect per-symbol failures.

        Args:
            symbols: Exact trading symbols to scan, or ``None`` for the default list.
            exchange: Exchange segment that lists the instruments.
            to_date: Last history date in broker format, or today when omitted.

        Returns:
            Rows sorted by score and failures keyed by symbol.
        """
        end_date = to_date or date.today().strftime("%d-%b-%Y")
        rows: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for symbol in symbols or self._default_symbols:
            try:
                bars = self._market.daily_bars(symbol, exchange, self._from_date, end_date)
                rows.append(score_frame(symbol, bars))
            except Exception as error:  # A bad symbol should not hide other scan results.
                errors.append({"symbol": symbol, "error": str(error)[:160]})
        rows.sort(key=lambda row: row["score"], reverse=True)
        return rows, errors
