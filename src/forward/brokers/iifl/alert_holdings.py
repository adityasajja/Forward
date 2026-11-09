"""Read holdings used by intelligent alert rules."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from forward.brokers.iifl.account_read import IiflAccountReadAdapter
from forward.core.alert_ports import (
    AlertHoldingsPort,
)


class IiflAlertHoldingsAdapter(AlertHoldingsPort):
    """Read normalized held symbols from the active IIFL account."""

    def __init__(self, client_provider: Callable[[], Any]) -> None:
        """Create the adapter.

        Args:
            client_provider: Return an authenticated IIFL client.
        """
        self._client_provider = client_provider

    def held_symbols(self) -> dict[str, dict[str, Any]]:
        """Return delivery holdings and net-long positions.

        Returns:
            Symbols keyed to their average price and held quantity.
        """
        held: dict[str, dict[str, Any]] = {}
        account = IiflAccountReadAdapter(self._client_provider())

        result = account.section("holdings", include_quotes=False)
        if result.error:
            raise RuntimeError(f"IIFL holdings read failed: {result.error}")
        for row in result.rows:
            symbol = str(
                row.get("nseTradingSymbol")
                or row.get("bseTradingSymbol")
                or row.get("symbol")
                or ""
            ).strip().upper()
            quantity_value = next(
                (
                    row[key]
                    for key in (
                        "totalQuantity",
                        "quantity",
                        "Quantity",
                        "holdingQuantity",
                    )
                    if row.get(key) is not None
                ),
                None,
            )
            if not symbol or quantity_value is None:
                raise ValueError("IIFL holding row has no symbol or quantity")
            quantity = float(quantity_value)
            if quantity != 0:
                held[symbol] = {
                    "avg_price": row.get("averageTradedPrice"),
                    "qty": quantity,
                }

        result = account.section("positions")
        if result.error:
            raise RuntimeError(f"IIFL positions read failed: {result.error}")
        for row in result.rows:
            quantity_value = next(
                (
                    row[key]
                    for key in ("netQuantity", "NetQuantity", "quantity")
                    if row.get(key) is not None
                ),
                None,
            )
            if quantity_value is None:
                raise ValueError("IIFL position row has no net quantity")
            quantity = float(quantity_value)
            if quantity <= 0:
                continue
            symbol = str(row.get("tradingSymbol") or row.get("symbol") or "").strip().upper()
            if not symbol:
                raise ValueError("IIFL position row has no trading symbol")
            held.setdefault(
                symbol,
                {
                    "avg_price": row.get("averagePrice", row.get("avgPrice")),
                    "qty": quantity,
                },
            )

        return held
