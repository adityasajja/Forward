"""IIFL Capital execution adapter.

Translates our domain :class:`Order` into the IIFL order payload and their
response rows back into domain objects.

Order payload fields (per docs, Order Management):
exchange, instrumentId, transactionType, quantity, product, orderComplexity,
orderType, price, slTriggerPrice, slLegPrice, targetLegPrice, validity,
disclosedQuantity, marketProtectionPercent, apiOrderSource, algoId, orderTag
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from loguru import logger

from forward.brokers.base import Broker, BrokerError
from forward.brokers.iifl.client import IiflClient
from forward.brokers.iifl.contracts import InstrumentMaster
from forward.brokers.iifl.responses import broker_error, empty_state, rows
from forward.core.enums import OrderStatus, OrderType, Side
from forward.core.instrument_models import Instrument
from forward.core.order_models import Fill, Order
from forward.core.portfolio_models import Funds, Holding, Position

# IIFL product codes
PRODUCT_INTRADAY = "INTRADAY"
PRODUCT_DELIVERY = "DELIVERY"
PRODUCT_NORMAL = "NORMAL"

_ORDER_TYPE_MAP = {
    OrderType.MARKET: "MARKET",
    OrderType.LIMIT: "LIMIT",
    OrderType.STOP: "SLM",
    OrderType.STOP_LIMIT: "SL",
}

_STATUS_MAP = {
    "OPEN": OrderStatus.SUBMITTED,
    "PENDING": OrderStatus.PENDING,
    "PARTIALLY FILLED": OrderStatus.PARTIALLY_FILLED,
    "PARTIALLYFILLED": OrderStatus.PARTIALLY_FILLED,
    "COMPLETE": OrderStatus.FILLED,
    "FILLED": OrderStatus.FILLED,
    "CANCELLED": OrderStatus.CANCELLED,
    "CANCELED": OrderStatus.CANCELLED,
    "REJECTED": OrderStatus.REJECTED,
}


def _pick(row: dict, *keys: str, default=None):
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return default


def _required_number(row: dict, field: str, *keys: str) -> float:
    """Read a required finite number from a broker record.

    Args:
        row: Broker record to inspect.
        field: Domain field name used in error messages.
        keys: Broker spellings for that field.

    Returns:
        The numeric value reported by IIFL.

    Raises:
        ValueError: If the field is missing or is not finite.
    """
    raw = _pick(row, *keys)
    if raw is None:
        raise ValueError(f"IIFL row has no {field}")
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError(f"IIFL row has a non-finite {field}")
    return value


def _required_side(row: dict) -> Side:
    """Read the explicit BUY or SELL direction from a broker record."""
    value = _pick(row, "transactionType", "TransactionType")
    if not isinstance(value, str):
        raise ValueError("IIFL row has no transaction type")
    try:
        return Side[value.strip().upper()]
    except KeyError as exc:
        raise ValueError(f"IIFL row has an unknown transaction type: {value!r}") from exc


def _response_rows(payload: Any, endpoint: str) -> list[dict[str, Any]]:
    """Validate one IIFL response and return its records.

    Args:
        payload: Raw response from an IIFL endpoint.
        endpoint: Endpoint label used in error messages.

    Returns:
        Valid broker record rows, excluding documented empty-state rows.

    Raises:
        BrokerError: If IIFL reports an error or returns malformed data.
    """
    error = broker_error(payload)
    if error:
        raise BrokerError(f"IIFL {endpoint} failed: {error}")
    try:
        return [row for row in rows(payload) if not empty_state(row)]
    except ValueError as exc:
        raise BrokerError(f"invalid IIFL {endpoint} response: {exc}") from exc


class IiflBroker(Broker):
    def __init__(
        self,
        client: IiflClient,
        master: InstrumentMaster,
        *,
        default_product: str,
        api_order_source: str,
        algo_id: str | None = None,
        market_protection_percent: float,
    ) -> None:
        self.client = client
        self.master = master
        self.default_product = default_product
        self.api_order_source = api_order_source
        self.algo_id = algo_id
        self.market_protection_percent = market_protection_percent

    # ------------------------------------------------------------------
    def build_payload(self, order: Order) -> dict[str, Any]:
        inst = order.instrument
        if inst.conid is None:
            raise BrokerError(f"instrument {inst.symbol} has no IIFL instrumentId")
        params = order.broker_params

        product = params.get("product")
        if product is None:
            product = PRODUCT_INTRADAY if inst.is_derivative else self.default_product

        payload: dict[str, Any] = {
            "exchange": inst.exchange.upper(),
            "instrumentId": str(inst.conid),
            "transactionType": "BUY" if order.side is Side.BUY else "SELL",
            "quantity": int(order.quantity),
            "product": str(product).upper(),
            "orderComplexity": str(params.get("orderComplexity", "REGULAR")).upper(),
            "orderType": _ORDER_TYPE_MAP[order.order_type],
            # An explicit broker_param wins (BO/CO legs need their own), but
            # the order's own TIF is the default — silently sending DAY for
            # an IOC/FOK turns a kill-or-fill into a resting order.
            "validity": str(params.get("validity", order.tif.value)).upper(),
        }
        if order.order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT) and order.limit_price:
            payload["price"] = round(order.limit_price, 2)
        if order.order_type in (OrderType.STOP, OrderType.STOP_LIMIT) and order.stop_price:
            payload["slTriggerPrice"] = round(order.stop_price, 2)
        if params.get("slLegPrice") is not None:
            payload["slLegPrice"] = params["slLegPrice"]
        if params.get("targetLegPrice") is not None:
            payload["targetLegPrice"] = params["targetLegPrice"]
        if params.get("disclosedQuantity"):
            payload["disclosedQuantity"] = int(params["disclosedQuantity"])
        # SEBI's algo framework (fully mandatory since 2026-04-01) requires
        # market orders sent through an API to carry a non-zero market
        # protection value; a zero or absent value is rejected. Send a default
        # for MARKET orders, and let an explicit broker_param override it.
        protection = params.get("marketProtectionPercent")
        if protection is None and order.order_type is OrderType.MARKET:
            protection = self.market_protection_percent
        if protection is not None:
            payload["marketProtectionPercent"] = float(protection)

        payload["apiOrderSource"] = params.get("apiOrderSource", self.api_order_source)
        if self.algo_id or params.get("algoId"):
            payload["algoId"] = params.get("algoId", self.algo_id)
        if order.tag:
            payload["orderTag"] = order.tag[:40]
        return payload

    # ------------------------------------------------------------------
    def place_order(self, order: Order) -> Order:
        payload = self.build_payload(order)
        logger.debug("placing IIFL order: {}", payload)
        result = self.client.place_orders([payload])
        if not result:
            raise BrokerError(f"empty response placing order for {order.instrument.symbol}")
        row = result[0] if isinstance(result, list) else result

        broker_id = str(_pick(row, "brokerOrderId", "BrokerOrderId", "orderId", default=""))
        if not broker_id:
            reason = _pick(row, "message", "Message")
            if reason is None:
                raise BrokerError("IIFL order response has no broker ID or rejection reason")
            order.mark(OrderStatus.REJECTED, reason=str(reason))
            raise BrokerError(f"order rejected: {reason}")
        order.broker_order_id = broker_id
        order.status = OrderStatus.SUBMITTED
        order.tag = order.tag or str(_pick(row, "orderTag", default="") or "")
        return order

    def modify_order(self, broker_order_id: str, **fields: Any) -> Order:
        payload = {k: v for k, v in fields.items() if v is not None}
        result = self.client.modify_order(broker_order_id, **payload)
        return self._order_from_row(result, self._instrument_for(result))

    def cancel_order(self, broker_order_id: str) -> bool:
        result = self.client.cancel_order(broker_order_id)
        if not isinstance(result, dict):
            return False
        status = str(_pick(result, "status", "Status", default="")).upper()
        return status in ("OK", "SUCCESS", "0")

    # ------------------------------------------------------------------
    def open_orders(self) -> list[Order]:
        records = _response_rows(self.client.order_book(), "order book")
        orders: list[Order] = []
        for index, row in enumerate(records):
            try:
                orders.append(self._order_from_row(row, self._instrument_for(row)))
            except (KeyError, ValueError) as exc:
                raise BrokerError(f"invalid IIFL order-book row {index}: {exc}") from exc
        return orders

    def positions(self) -> list[Position]:
        records = _response_rows(self.client.positions(), "positions")
        out = []
        for row in records:
            instrument = self._instrument_for(row)
            qty = _required_number(
                row, "net quantity", "netQuantity", "NetQuantity", "quantity", "Quantity"
            )
            if qty == 0:
                continue
            avg = _required_number(row, "average price", "averagePrice", "AveragePrice", "avgPrice")
            ltp = _required_number(row, "last price", "ltp", "LTP", "lastTradedPrice")
            if avg <= 0 or ltp <= 0:
                raise BrokerError(f"IIFL position {instrument.symbol} has a non-positive price")
            out.append(
                Position(
                    instrument=instrument,
                    quantity=qty,
                    avg_price=avg,
                    last_price=ltp,
                    realized_pnl=_required_number(
                        row, "realized P&L", "realizedPnl", "realisedPnl", "pnl"
                    ),
                )
            )
        return out

    def last_price(self, instruments: list[Instrument]) -> dict[str, float]:
        if any(instrument.conid is None for instrument in instruments):
            raise BrokerError("every IIFL quote instrument must have an instrument ID")
        symbols_by_key = {
            (instrument.exchange.upper(), str(instrument.conid)): instrument.symbol
            for instrument in instruments
        }
        legs = list(symbols_by_key)
        quote_rows = _response_rows(self.client.market_quotes(legs), "market quotes")
        out: dict[str, float] = {}
        for index, row in enumerate(quote_rows):
            exchange = _pick(row, "exchange", "Exchange")
            instrument_id = _pick(row, "instrumentId", "InstrumentId")
            if exchange is None or instrument_id is None:
                raise BrokerError(f"invalid IIFL quote row {index}: contract identity is missing")
            key = (str(exchange).upper(), str(instrument_id))
            symbol = symbols_by_key.get(key)
            if symbol is None:
                raise BrokerError(f"IIFL returned an unrequested quote contract: {key}")
            ltp = _pick(row, "ltp", "LTP", "lastTradedPrice", "lastPrice")
            if ltp is not None:
                price = float(ltp)
                if not math.isfinite(price) or price <= 0:
                    raise BrokerError(f"IIFL returned an invalid last price for {symbol}")
                out[symbol] = price
        return out

    # ------------------------------------------------------------------
    def holdings(self) -> list[Holding]:
        """Settled holdings, for reconciliation against the platform's positions.

        A holding and a position are not the same thing: on a T+1 market a purchase
        is a position today and a holding tomorrow. That is why the reconciler
        treats a position mismatch as critical and a holding mismatch as a warning —
        the difference is usually settlement, and calling it critical would train
        an operator to ignore the alarm.
        """
        records = _response_rows(self.client.holdings(), "holdings")
        out = []
        for row in records:
            instrument = self._instrument_for(row)
            quantity = _required_number(
                row,
                "holding quantity",
                "quantity",
                "Quantity",
                "holdingQuantity",
                "totalQty",
                "totalQuantity",
            )
            if quantity == 0:
                continue
            average_price = _required_number(
                row,
                "average price",
                "averagePrice",
                "AveragePrice",
                "avgPrice",
                "averageTradedPrice",
            )
            last_price = _required_number(row, "last price", "ltp", "LTP", "lastPrice")
            if average_price <= 0 or last_price <= 0:
                raise BrokerError(f"IIFL holding {instrument.symbol} has a non-positive price")
            sellable = _pick(row, "sellableQuantity", "sellableQty", "freeQty", "availableQty")
            out.append(
                Holding(
                    instrument=instrument,
                    quantity=quantity,
                    avg_price=average_price,
                    last_price=last_price,
                    sellable_quantity=float(sellable) if sellable is not None else None,
                    isin=_pick(row, "isin", "ISIN"),
                    raw=row,
                )
            )
        return out

    def funds(self) -> Funds:
        """Cash and margin, from the broker's limits endpoint.

        The field names differ between broker environments, so this reads the
        common spellings and keeps the raw payload. It deliberately does **not**
        guess: if none of the known keys is present, ``available_cash`` stays at
        ``0.0`` and ``raw`` carries the payload, so a reconciler can see that the
        figure was absent rather than conclude the account is empty.
        """
        payload = self.client.limits()
        if not isinstance(payload, dict):
            raise BrokerError("IIFL limits response must be an object")
        row = payload
        # Some environments nest the figures one level down.
        for key in ("limits", "data", "result"):
            nested = row.get(key)
            if isinstance(nested, dict):
                row = {**row, **nested}

        cash = _pick(
            row,
            "availableCash",
            "availablecash",
            "availableMargin",
            "netAvailableMargin",
            "cashMarginAvailable",
            "availableBalance",
            "net",
        )
        margin = _pick(row, "marginUsed", "utilisedMargin", "usedMargin", "marginUtilised")
        withdrawable = _pick(row, "withdrawableAmount", "withdrawable", "netWithdrawable")
        collateral = _pick(row, "collateralValue", "collateral", "totalCollateral")
        if cash is None or margin is None:
            raise BrokerError("IIFL limits response is missing cash or used margin")
        return Funds(
            available_cash=float(cash),
            margin_used=float(margin),
            withdrawable=float(withdrawable) if withdrawable is not None else None,
            collateral=float(collateral) if collateral is not None else None,
            raw=row,
        )

    # ------------------------------------------------------------------
    def margin_required(self, order: Order) -> dict[str, Any]:
        payload = self.build_payload(order)
        return self.client.preorder_margin(payload)

    # ------------------------------------------------------------------
    def _instrument_for(self, row: dict) -> Instrument:
        """Resolve a broker row to an exact listed contract.

        Args:
            row: Broker record with its trading symbol and exchange.

        Returns:
            The matching instrument from the contract master.

        Raises:
            KeyError: If the broker record does not match a listed contract.
            ValueError: If the broker record has no symbol or exchange.
        """
        symbol = _pick(row, "tradingSymbol", "TradingSymbol", "nseTradingSymbol", "symbol")
        exchange = _pick(row, "exchange", "Exchange")
        if not symbol or not exchange:
            raise ValueError("broker record must include trading symbol and exchange")
        return self.master.find(str(symbol), str(exchange).upper())

    @staticmethod
    def _order_from_row(row: dict, instrument: Instrument) -> Order:
        side = _required_side(row)
        status_value = _pick(row, "orderStatus", "OrderStatus")
        if not isinstance(status_value, str):
            raise ValueError("order row has no status")
        status_text = status_value.strip().upper()
        if status_text not in _STATUS_MAP:
            raise ValueError(f"order row has an unknown status: {status_text!r}")
        qty = _required_number(row, "quantity", "quantity", "Quantity")
        filled = _required_number(row, "filled quantity", "filledQuantity", "FilledQuantity")
        avg_raw = _pick(row, "averageTradedPrice", "AverageTradedPrice")
        avg = 0.0 if avg_raw is None and filled == 0 else _required_number(
            row, "average fill price", "averageTradedPrice", "AverageTradedPrice"
        )

        if qty <= 0:
            # A row with no quantity is not a 1-share order. Refuse the
            # response rather than hiding a live order from reconciliation.
            raise ValueError(f"order row has no quantity: {row!r}")
        if filled < 0 or filled > qty:
            raise ValueError(f"order row has invalid filled quantity: {filled}")
        if filled > 0 and avg <= 0:
            raise ValueError("filled order row has no positive average fill price")

        return Order(
            instrument=instrument,
            side=side,
            quantity=qty,
            status=_STATUS_MAP[status_text],
            filled_quantity=filled,
            avg_fill_price=avg,
            broker_order_id=str(_pick(row, "brokerOrderId", "BrokerOrderId", default="") or ""),
            tag=str(_pick(row, "orderTag", "OrderTag", default="") or "") or None,
        )

    def fills_from_trades(self) -> list[Fill]:
        """Reconstruct :class:`Fill` objects from /trades (used to reconcile on
        startup or after a reconnect)."""
        records = _response_rows(self.client.trades(), "trades")
        fills: list[Fill] = []
        for index, row in enumerate(records):
            instrument = self._instrument_for(row)
            qty = _required_number(
                row, "filled quantity", "filledQuantity", "FilledQuantity", "quantity"
            )
            price = _required_number(
                row, "trade price", "tradedPrice", "TradedPrice", "averageTradedPrice"
            )
            if qty <= 0 or price <= 0:
                raise BrokerError(
                    f"invalid IIFL trade row {index}: quantity and price must be positive"
                )
            side = _required_side(row)
            broker_order_id = _pick(row, "brokerOrderId", "BrokerOrderId")
            if broker_order_id is None:
                raise BrokerError(f"invalid IIFL trade row {index}: broker order ID is missing")
            fills.append(
                Fill(
                    order_id=str(broker_order_id),
                    instrument=instrument,
                    side=side,
                    quantity=qty,
                    price=price,
                    ts=_parse_ts(_pick(row, "fillTimestamp", "exchangeTimestamp", "fillTime")),
                )
            )
        return fills


def _parse_ts(value: Any) -> datetime:
    """Parse a broker fill timestamp without inventing a replacement time.

    Args:
        value: Timestamp value returned by the broker.

    Returns:
        Parsed broker timestamp.

    Raises:
        ValueError: If the broker did not provide a recognized timestamp.
    """
    if value is None:
        raise ValueError("broker fill has no timestamp")
    if isinstance(value, datetime):
        return value
    text = str(value)
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d-%b-%Y %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    raise ValueError(f"unrecognized broker fill timestamp: {value!r}")
