"""Match paper orders against explicit prices and execution costs."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from forward.config.costs import indian_delivery_costs, slippage_model
from forward.core.costs import CommissionModel, SlippageModel
from forward.core.enums import OrderType, Side
from forward.core.instrument_models import Instrument
from forward.core.order_ports import OrderInstrumentLookupPort
from forward.core.paper_models import PaperPriceSourcePort, TickPrice
from forward.core.time import utcnow
from forward.execution.venue import (
    VENUE_ACCEPTED,
    VENUE_FILLED,
    VENUE_PARTIAL,
    VENUE_REJECTED,
    VenueOutcome,
)


def _is_valid_price(price: float | None) -> bool:
    """Return whether a price is finite and positive."""
    return price is not None and math.isfinite(price) and price > 0


@dataclass(frozen=True)
class MatchDecision:
    """Whether an order fills now, and at what price."""

    fills: bool
    price: float | None = None
    reason: str | None = None

    @property
    def execution_price(self) -> float:
        """Return the validated price for a fill decision.

        Raises:
            RuntimeError: If this decision does not contain a usable fill price.
        """
        if not self.fills or not _is_valid_price(self.price):
            raise RuntimeError("fill decision must contain a positive finite price")
        return self.price

def match_order(
    *,
    order_type: str,
    side: str,
    reference: float | None,
    limit_price: float | None = None,
    stop_price: float | None = None,
) -> MatchDecision:
    """Decide whether an order fills at ``reference``.

    Pure, so every rule below is testable without a venue, a cache or a clock.

    ``reference`` is the price the market is at. For a MARKET order that is the
    fill; for the others it is what the order is measured against.
    """
    if not _is_valid_price(reference):
        # Not "no fill" — unpriceable. The caller turns this into a rejection,
        # because a resting order with no price is one that can never fill and
        # would sit in the book forever looking live.
        return MatchDecision(False, reason="no price available")

    kind = OrderType.parse(order_type)
    parsed_side = Side.parse(side)
    buying = parsed_side is Side.BUY

    if kind is OrderType.MARKET:
        return MatchDecision(True, reference)

    if kind is OrderType.LIMIT:
        if limit_price is None or limit_price <= 0:
            return MatchDecision(False, reason="a limit order needs a limit price")
        # A buy fills at or below its limit; a sell at or above. Filling a buy
        # limit above the limit is the classic paper-trading lie — it manufactures
        # edge the market never offered.
        if buying and reference <= limit_price:
            return MatchDecision(True, reference)
        if not buying and reference >= limit_price:
            return MatchDecision(True, reference)
        return MatchDecision(
            False,
            reason=(
                f"resting: market {reference:,.2f} has not reached the limit {limit_price:,.2f}"
            ),
        )

    # STOP and STOP_LIMIT share the trigger; STOP_LIMIT then applies its limit.
    if stop_price is None or stop_price <= 0:
        return MatchDecision(False, reason="a stop order needs a trigger price")
    triggered = reference <= stop_price if not buying else reference >= stop_price
    if not triggered:
        return MatchDecision(
            False,
            reason=(
                f"resting: market {reference:,.2f} has not reached the trigger {stop_price:,.2f}"
            ),
        )
    if kind is OrderType.STOP:
        return MatchDecision(True, reference)
    # STOP_LIMIT: triggered, but the limit can still refuse the fill.
    if limit_price is None or limit_price <= 0:
        return MatchDecision(False, reason="a stop-limit order needs a limit price")
    if buying and reference <= limit_price:
        return MatchDecision(True, reference)
    if not buying and reference >= limit_price:
        return MatchDecision(True, reference)
    return MatchDecision(
        False, reason=f"triggered but the limit {limit_price:,.2f} was skipped past"
    )

@dataclass
class PaperVenue:
    """Fills orders against a reference price, with slippage and Indian costs.

    The cost model defaults to :class:`~forward.core.costs.IndianDeliveryCosts`
    and **not** the IBKR-style default, because that default understates NSE
    delivery by roughly 28x. A paper account costed with it reports an edge that
    does not exist, which is the specific error the whole research layer in this
    repo exists to avoid.
    """

    prices: PaperPriceSourcePort
    instruments: OrderInstrumentLookupPort
    adv: Callable[[str, str], float | None]
    #: Maximum allowable age for a reference tick in seconds. If exceeded, orders are rejected.
    max_tick_age_seconds: float | None
    slippage: SlippageModel = field(default_factory=slippage_model)
    costs: CommissionModel = field(default_factory=indian_delivery_costs)
    #: Fraction of the order that fills immediately. 1.0 is the optimistic case;
    #: a value below it makes partial fills part of the simulation rather than a
    #: thing that only happens in live trading.
    fill_ratio: float = 1.0
    clock: Callable[[], datetime] = field(default=utcnow)
    #: Average daily volume for a symbol. Missing or invalid volume prevents a paper fill.
    max_participation: float = 0.05
    #: Impact in bps = this x sqrt(order / ADV). 100 means 1% of a day's volume costs ~10 bps.
    impact_coefficient_bps: float = 100.0

    def __post_init__(self) -> None:
        """Validate the configured fill and liquidity limits."""
        if not math.isfinite(self.fill_ratio) or not 0 < self.fill_ratio <= 1:
            raise ValueError("fill_ratio must be finite and in (0, 1]")
        if self.max_tick_age_seconds is not None and (
            not math.isfinite(self.max_tick_age_seconds) or self.max_tick_age_seconds <= 0
        ):
            raise ValueError("max_tick_age_seconds must be positive and finite or None")
        if not math.isfinite(self.max_participation) or not 0 < self.max_participation <= 1:
            raise ValueError("max_participation must be finite and in (0, 1]")
        if not math.isfinite(self.impact_coefficient_bps) or self.impact_coefficient_bps < 0:
            raise ValueError("impact_coefficient_bps must be finite and non-negative")

    def submit(self, draft: Any) -> VenueOutcome:
        """Fill or rest. See :func:`match_order` for the rules."""
        instrument = self._instrument(draft)
        raw_reference = self._price_raw(draft.symbol, draft.exchange)
        reference, tick_ts, price_age = self._extract_price_and_age(raw_reference)

        # Freshness check: reject stale prices
        if (
            self.max_tick_age_seconds is not None
            and (price_age is None or price_age > self.max_tick_age_seconds)
        ):
            age_reason = (
                "tick timestamp is unavailable"
                if price_age is None
                else f"tick age {price_age:.1f}s exceeds max acceptable "
                f"{self.max_tick_age_seconds:.1f}s"
            )
            return VenueOutcome(
                status=VENUE_REJECTED,
                reject_reason=f"unusable price for {draft.symbol}: {age_reason}",
                raw={
                    "reference_price": reference,
                    "price_timestamp": tick_ts.isoformat() if tick_ts else None,
                    "price_age_seconds": price_age,
                    "stale": True,
                },
            )

        decision = match_order(
            order_type=draft.order_type,
            side=draft.side,
            reference=reference,
            limit_price=draft.limit_price,
            stop_price=draft.stop_price,
        )
        if not decision.fills:
            if not _is_valid_price(reference):
                # Unpriceable is a *rejection*, not a resting order: an order that
                # can never fill would otherwise sit in the book looking live.
                return VenueOutcome(
                    status=VENUE_REJECTED,
                    reject_reason=(
                        f"no price available for {draft.symbol} — refusing to fill an "
                        "order that cannot be priced"
                    ),
                    raw={"reference_price": None},
                )
            return VenueOutcome(
                status=VENUE_ACCEPTED,
                broker_order_id=f"PAPER-{draft.symbol}-resting",
                raw={
                    "reference_price": reference,
                    "price_timestamp": tick_ts.isoformat() if tick_ts else None,
                    "price_age_seconds": price_age,
                    "resting_reason": decision.reason,
                },
            )

        return self._fill(
            draft,
            instrument,
            decision.execution_price,
            reference,
            tick_ts,
            price_age,
            quote=raw_reference if isinstance(raw_reference, TickPrice) else None,
        )

    def match(self, order: dict[str, Any]) -> VenueOutcome:
        """Re-evaluate a resting order against the current price.

        This is what makes a paper LIMIT or STOP order behave like one that is
        actually live, rather than one that fills instantly and always.
        """
        raw_reference = self._price_raw(order["symbol"], order["exchange"])
        reference, tick_ts, price_age = self._extract_price_and_age(raw_reference)

        # Freshness check on resting match
        if (
            self.max_tick_age_seconds is not None
            and (price_age is None or price_age > self.max_tick_age_seconds)
        ):
            age_reason = (
                "tick timestamp is unavailable"
                if price_age is None
                else f"stale tick ({price_age:.1f}s)"
            )
            return VenueOutcome(
                status=VENUE_ACCEPTED,
                broker_order_id=order.get("broker_order_id"),
                raw={
                    "reference_price": reference,
                    "price_timestamp": tick_ts.isoformat() if tick_ts else None,
                    "price_age_seconds": price_age,
                    "resting_reason": age_reason,
                },
            )

        last_touch = order.get("updated_at")
        if (
            self.adv is not None
            and float(order.get("filled_quantity") or 0.0) > 0
            and getattr(last_touch, "date", None) is not None
            and last_touch.date() == self.clock().date()
        ):
            # A partly-filled order already took today's volume allowance; it does not take another
            # slice every poll. (The order row's own timestamp, so this survives a restart.)
            return VenueOutcome(
                status=VENUE_ACCEPTED,
                broker_order_id=order.get("broker_order_id"),
                raw={"resting_reason": "today's volume allowance for this order is used up"},
            )

        decision = match_order(
            order_type=order["order_type"],
            side=order["side"],
            reference=reference,
            limit_price=order.get("limit_price"),
            stop_price=order.get("stop_price"),
        )
        if not decision.fills:
            return VenueOutcome(
                status=VENUE_ACCEPTED,
                broker_order_id=order.get("broker_order_id"),
                raw={
                    "reference_price": reference,
                    "price_timestamp": tick_ts.isoformat() if tick_ts else None,
                    "price_age_seconds": price_age,
                    "resting_reason": decision.reason,
                },
            )
        instrument = self._instrument_from(order["symbol"], order["exchange"])
        # The remaining quantity, not the original: an order that partially filled
        # has less left to do.
        outstanding = float(order["quantity"]) - float(order.get("filled_quantity") or 0.0)
        if outstanding <= 0:
            return VenueOutcome(
                status=VENUE_ACCEPTED,
                broker_order_id=order.get("broker_order_id"),
                raw={"resting_reason": "nothing left to fill"},
            )
        return self._fill_from(
            order,
            instrument,
            decision.execution_price,
            reference,
            outstanding,
            tick_ts,
            price_age,
        )

    def _liquidity(self, symbol: str, exchange: str, side: Side, price: float, quantity: float):
        """Apply the configured participation cap and market impact.

        Args:
            symbol: Symbol used to read average daily volume.
            exchange: Listing exchange for the symbol.
            side: Trade direction used to apply adverse impact.
            price: Price before market impact.
            quantity: Requested fill quantity.

        Returns:
            Price, capped quantity, and the liquidity facts for the fill.

        Raises:
            ValueError: If average daily volume is unavailable or cannot support
                one unit of the order.
        """
        details: dict[str, Any] = {}
        adv = self.adv(symbol, exchange)
        if adv is None or not math.isfinite(adv) or adv <= 0:
            raise ValueError(f"{symbol}: positive finite average daily volume is required")
        cap = float(math.floor(adv * self.max_participation))
        if cap < 1:
            raise ValueError(f"{symbol}: average daily volume cannot support one unit")
        if quantity > cap:
            details["capped_from"] = quantity
            quantity = cap
        impact_bps = self.impact_coefficient_bps * (quantity / adv) ** 0.5
        price += (price * impact_bps / 10_000.0) * (1 if side is Side.BUY else -1)
        details.update(adv=adv, participation=quantity / adv, impact_bps=round(impact_bps, 2))
        return price, quantity, details

    @staticmethod
    def _respect_limit(
        *, order_type: str, side: Side, price: float, limit_price: float | None
    ) -> float:
        """Keep execution friction inside a limit order's price boundary.

        Args:
            order_type: Type of order being filled.
            side: Whether the order buys or sells.
            price: Simulated price after slippage and market impact.
            limit_price: Maximum buy price or minimum sell price.

        Returns:
            The simulated price, capped at the order's limit when applicable.

        Raises:
            ValueError: If a limit order reaches execution without a valid limit.
        """
        kind = OrderType.parse(order_type)
        if kind not in (OrderType.LIMIT, OrderType.STOP_LIMIT):
            return price
        if not _is_valid_price(limit_price):
            raise ValueError("a limit fill requires a positive finite limit price")
        if side is Side.BUY:
            return min(price, float(limit_price))
        return max(price, float(limit_price))

    @staticmethod
    def _fill_status(*, filled_quantity: float, order_quantity: float) -> str:
        """Return the venue status for a cumulative or initial fill quantity.

        Args:
            filled_quantity: Quantity filled so far.
            order_quantity: Total quantity requested by the order.

        Returns:
            ``FILLED`` when the order is complete, otherwise ``PARTIAL``.
        """
        if filled_quantity >= order_quantity - 1e-9:
            return VENUE_FILLED
        return VENUE_PARTIAL

    def _fill(
        self,
        draft: Any,
        instrument: Instrument,
        raw_price: float,
        reference: float,
        tick_ts: datetime | None = None,
        price_age: float | None = None,
        quote: TickPrice | None = None,
    ) -> VenueOutcome:
        """Build a fill outcome for a newly submitted order."""
        side = Side.parse(draft.side)
        basis = "last_plus_flat_slippage"
        if quote is not None and OrderType.parse(draft.order_type) is OrderType.MARKET:
            if (
                not _is_valid_price(quote.bid)
                or not _is_valid_price(quote.ask)
                or quote.bid > quote.ask
            ):
                return VenueOutcome(
                    status=VENUE_REJECTED,
                    reject_reason=f"market order for {draft.symbol} needs a valid bid and ask",
                    raw={"reference_price": reference},
                )
            # Market orders cross the observed spread: buys pay ask, sells receive bid.
            price = float(quote.ask if side is Side.BUY else quote.bid)
            basis = "quote"
        else:
            price = self.slippage.apply(raw_price, side, instrument)
        quantity = abs(float(draft.quantity)) * self.fill_ratio
        if quantity <= 0:
            return VenueOutcome(
                status=VENUE_ACCEPTED, raw={"resting_reason": "fill_ratio produced no size"}
            )
        price, quantity, liquidity = self._liquidity(
            draft.symbol, draft.exchange, side, price, quantity
        )
        price = self._respect_limit(
            order_type=draft.order_type,
            side=side,
            price=price,
            limit_price=draft.limit_price,
        )
        commission = float(self.costs.compute(quantity, price, instrument, side))
        order_quantity = abs(float(draft.quantity))
        raw_payload: dict[str, Any] = {
            "reference_price": reference,
            "fill_price": price,
            "limit_price": draft.limit_price,
            "commission": commission,
            "slippage_bps": _bps(reference, price, side),
            "venue": "paper",
            "fill_basis": basis,
            **liquidity,
        }
        if tick_ts is not None:
            raw_payload["price_timestamp"] = tick_ts.isoformat()
        if price_age is not None:
            raw_payload["price_age_seconds"] = round(price_age, 3)

        return VenueOutcome(
            status=self._fill_status(
                filled_quantity=quantity,
                order_quantity=order_quantity,
            ),
            broker_order_id=f"PAPER-{draft.symbol}",
            filled_qty=quantity,
            filled_price=price,
            commission=commission,
            raw=raw_payload,
        )

    def _fill_from(
        self,
        order: dict[str, Any],
        instrument: Instrument,
        raw_price: float,
        reference: float,
        quantity: float,
        tick_ts: datetime | None = None,
        price_age: float | None = None,
    ) -> VenueOutcome:
        """Build a fill outcome for an order that was already resting."""
        side = Side.parse(order["side"])
        price = self.slippage.apply(raw_price, side, instrument)
        price, quantity, liquidity = self._liquidity(
            order["symbol"], order["exchange"], side, price, quantity
        )
        price = self._respect_limit(
            order_type=order["order_type"],
            side=side,
            price=price,
            limit_price=order.get("limit_price"),
        )
        commission = float(self.costs.compute(quantity, price, instrument, side))
        cumulative_filled = float(order.get("filled_quantity") or 0.0) + quantity
        order_quantity = float(order["quantity"])
        raw_payload: dict[str, Any] = {
            "reference_price": reference,
            "fill_price": price,
            "limit_price": order.get("limit_price"),
            "commission": commission,
            "slippage_bps": _bps(reference, price, side),
            "venue": "paper",
            "matched_resting_order": True,
            **liquidity,
        }
        if tick_ts is not None:
            raw_payload["price_timestamp"] = tick_ts.isoformat()
        if price_age is not None:
            raw_payload["price_age_seconds"] = round(price_age, 3)

        return VenueOutcome(
            status=self._fill_status(
                filled_quantity=cumulative_filled,
                order_quantity=order_quantity,
            ),
            broker_order_id=order.get("broker_order_id") or f"PAPER-{order['symbol']}",
            filled_qty=cumulative_filled,
            filled_price=price,
            commission=commission,
            raw=raw_payload,
        )

    def _extract_price_and_age(
        self, raw_val: float | TickPrice | None
    ) -> tuple[float | None, datetime | None, float | None]:
        """Normalize a price value and its optional timestamp and age."""
        if raw_val is None:
            return None, None, None
        if isinstance(raw_val, TickPrice):
            return raw_val.price, raw_val.timestamp, raw_val.get_age_seconds(now=self.clock())
        return float(raw_val), None, None

    def _price_raw(self, symbol: str, exchange: str) -> float | TickPrice | None:
        """Read the configured price source for one instrument."""
        return self.prices(symbol, exchange)

    def _instrument(self, draft: Any) -> Instrument:
        """Resolve the order draft's instrument."""
        return self._instrument_from(draft.symbol, draft.exchange)

    def _instrument_from(self, symbol: str, exchange: str) -> Instrument:
        """Resolve the exact listed instrument used to calculate a fill."""
        return self.instruments(symbol, exchange)

def _bps(reference: float, filled: float, side: Side) -> float | None:
    """Adverse-positive slippage in bps, same convention as the OMS."""
    if not reference:
        return None
    raw = (filled - reference) / reference
    if side is Side.SELL:
        raw = -raw
    return raw * 10_000.0

def paper_venue(
    prices: PaperPriceSourcePort,
    *,
    instruments: OrderInstrumentLookupPort,
    adv: Callable[[str, str], float | None],
    **kwargs: Any,
) -> PaperVenue:
    """Build a paper venue with an explicit price source.

    Args:
        prices: Live quote lookup used to price simulated fills.
        instruments: Lookup for exact listed contract details.
        adv: Required source of average daily volume for the listing.
        **kwargs: Paper venue settings.

    Returns:
        A paper venue that uses the supplied price source.
    """
    return PaperVenue(prices=prices, instruments=instruments, adv=adv, **kwargs)
