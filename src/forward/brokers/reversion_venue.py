"""Quote-only IOC paper matching with explicit adverse execution and price limits."""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from forward.brokers.paper_venue import PaperVenue
from forward.core.enums import Side
from forward.core.paper_models import TickPrice
from forward.execution.venue import VENUE_ACCEPTED, VENUE_FILLED, VENUE_REJECTED, VenueOutcome


class ReversionPaperVenue(PaperVenue):
    parameters: Mapping[str, Any]

    def submit(self, draft):
        quote = self._price_raw(draft.symbol, draft.exchange)
        if (not isinstance(quote, TickPrice) or quote.timestamp is None
                or quote.age_seconds is None
                or not 0 <= quote.age_seconds <= self.parameters["max_quote_age_seconds"]
                or not quote.bid or not quote.ask or not 0 < quote.bid <= quote.ask):
            return VenueOutcome(status=VENUE_REJECTED, reject_reason="Fresh executable bid/ask required")
        side = Side.BUY if draft.side == "BUY" else Side.SELL
        adv = self.adv(draft.symbol, draft.exchange) if self.adv else None
        if not adv or not math.isfinite(adv) or adv <= 0:
            return VenueOutcome(status=VENUE_REJECTED, reject_reason="Verified prior-session ADV required")
        quantity = math.floor(
            min(
                float(draft.quantity) * self.fill_ratio,
                adv * self.parameters["max_adv_participation"],
            )
        )
        if quantity < 1:
            return VenueOutcome(status=VENUE_ACCEPTED, raw={"resting_reason": "No executable whole-share quantity"})
        sign = 1 if side is Side.BUY else -1
        reference = quote.ask if sign > 0 else quote.bid
        impact = (
            self.parameters["impact_multiplier"]
            * math.sqrt(quantity / adv)
            / self.parameters["impact_scale_bps"]
        )
        price = (
            reference
            * (1 + sign * self.parameters["entry_price_pad_pct"])
            * (1 + sign * impact)
        )
        if draft.limit_price is not None and sign*(price-draft.limit_price) > 1e-10:
            return VenueOutcome(status=VENUE_ACCEPTED, raw={"resting_reason": "IOC price limit exceeded"})
        instrument = self._instrument(draft)
        fees = self.costs.compute(quantity, price, instrument, side)
        return VenueOutcome(status=VENUE_FILLED, broker_order_id=f"PAPER-{draft.symbol}",
            filled_qty=quantity, filled_price=price, commission=fees,
            raw={"venue": "paper", "fill_basis": "bid_ask_plus_10bps_and_ADV_impact", "reference_price": reference,
                 "fill_price": price, "commission": fees, "price_timestamp": quote.timestamp.isoformat(),
                 "price_age_seconds": quote.age_seconds, "impact_bps": impact*10000})
