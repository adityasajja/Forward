"""IIFL adapter for read-only account sections."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any

from forward.brokers.iifl.client import IiflClient
from forward.brokers.iifl.responses import broker_error, empty_state, rows
from forward.core.market_models import BrokerReadResult


class IiflAccountReadAdapter:
    """Read account records and holding quotes from an authenticated IIFL client."""

    def __init__(self, client: IiflClient) -> None:
        """Create the adapter.

        Args:
            client: Authenticated IIFL client for the current request.
        """
        self._client = client

    @property
    def session_identity(self) -> str:
        """Return a non-secret identity for coalescing overlapping reads."""
        session = self._client.session
        if session is None or not session.user_session:
            raise RuntimeError("an authenticated IIFL session is required for account reads")
        return hashlib.sha256(session.user_session.encode()).hexdigest()

    def section(
        self, name: str, *, include_quotes: bool = True
    ) -> BrokerReadResult:
        """Read one account section and normalize its IIFL response.

        Args:
            name: Account section to read.
            include_quotes: Whether holdings should include current quotes.

        Returns:
            Normalized records and any IIFL error message.
        """
        readers: dict[str, Callable[[], Any]] = {
            "limits": self._client.limits,
            "positions": self._client.positions,
            "holdings": self._client.holdings,
            "orders": self._client.order_book,
            "trades": self._client.trades,
        }
        payload = readers[name]()
        error = broker_error(payload)
        if error:
            return BrokerReadResult(rows=[], error=error)

        records = [row for row in rows(payload) if not empty_state(row)]
        if name == "holdings":
            records = [row for row in records if self._holding_quantity(row) != 0]
            if include_quotes:
                try:
                    self._attach_last_price(records)
                except Exception as exc:
                    return BrokerReadResult(rows=[], error=str(exc))
        return BrokerReadResult(rows=records)

    def _attach_last_price(self, records: list[dict[str, Any]]) -> None:
        """Add current market quotes to holding rows."""
        by_nse: dict[str, list[dict[str, Any]]] = {}
        by_bse: dict[str, list[dict[str, Any]]] = {}
        for record in records:
            nse_id = record.get("nseInstrumentId")
            bse_id = record.get("bseInstrumentId")
            if nse_id:
                by_nse.setdefault(str(nse_id), []).append(record)
            elif bse_id:
                by_bse.setdefault(str(bse_id), []).append(record)

        legs = [("NSEEQ", instrument_id) for instrument_id in by_nse]
        legs.extend(("BSEEQ", instrument_id) for instrument_id in by_bse)
        if not legs:
            return

        payload = self._client.market_quotes(legs)
        error = broker_error(payload)
        if error:
            raise RuntimeError(error)
        quotes = rows(payload)

        for quote in quotes:
            raw_price = quote.get("ltp")
            if raw_price is None:
                continue
            price = float(raw_price)
            if price <= 0:
                continue
            instrument_id = str(quote["instrumentId"])
            for record in by_nse.get(instrument_id, []):
                record["ltp"] = price
            for record in by_bse.get(instrument_id, []):
                record["ltp"] = price

    @staticmethod
    def _holding_quantity(record: dict[str, Any]) -> float:
        """Return the quantity field used to decide whether a row is held."""
        quantity = next(
            (
                record[key]
                for key in (
                    "quantity",
                    "Quantity",
                    "holdingQuantity",
                    "totalQty",
                    "totalQuantity",
                )
                if record.get(key) is not None
            ),
            None,
        )
        if quantity is None:
            raise ValueError("IIFL holding row has no quantity")
        return float(quantity)
