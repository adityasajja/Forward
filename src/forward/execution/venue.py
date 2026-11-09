"""Port and result types for order venues."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from forward.execution.oms import OrderDraft

#: Venue outcomes. Deliberately not the OMS states — a venue reports what the
#: *exchange* said, and mapping that onto our lifecycle is this module's job.
VENUE_ACCEPTED = "ACCEPTED"
VENUE_PARTIAL = "PARTIAL"
VENUE_FILLED = "FILLED"
VENUE_REJECTED = "REJECTED"

VENUE_STATUSES = frozenset({VENUE_ACCEPTED, VENUE_PARTIAL, VENUE_FILLED, VENUE_REJECTED})
@dataclass(frozen=True)
class VenueOutcome:
    """What a venue said about one submission.

    Attributes:
        status: Accepted, partial, filled, or rejected result.
        broker_order_id: Broker reference when one was assigned.
        filled_qty: Cumulative filled quantity.
        filled_price: Average fill price when available.
        commission: Charges recorded for this execution, or ``None`` when unknown.
        reject_reason: Reason supplied for a rejection.
        raw: Original broker details needed for the event log.
    """

    status: str
    broker_order_id: str | None = None
    filled_qty: float = 0.0
    filled_price: float | None = None
    #: The frictions actually charged, in rupees. Carried through to the event log
    #: rather than recomputed later, so a paper account's net P&L is a fact about
    #: what happened and not a function of today's cost model.
    commission: float | None = None
    reject_reason: str | None = None
    raw: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.status not in VENUE_STATUSES:
            raise ValueError(
                f"venue status must be one of {sorted(VENUE_STATUSES)}, got {self.status!r}"
            )
        if self.status == VENUE_REJECTED and not (self.reject_reason or "").strip():
            # A rejection with no reason cannot be acted on, and it is the one
            # outcome where the reason is the whole value of the message.
            raise ValueError("a venue rejection must carry a reason")

    @property
    def filled(self) -> bool:
        return self.status in (VENUE_FILLED, VENUE_PARTIAL)


class Venue(Protocol):
    """Somewhere an order can be sent. Backtest, paper and live all satisfy this."""

    def submit(self, draft: OrderDraft) -> VenueOutcome:
        """Submit one order draft to the venue.

        Args:
            draft: Validated order intent.

        Returns:
            The venue's accepted, filled, or rejected result.
        """
