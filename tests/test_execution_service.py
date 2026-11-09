"""The execution service: one path from an intent to a venue.

The tests that matter here are the ones that pin the bugs this module was written
to remove:

* an order type is resolved **before** anything is transmitted, so a misspelling
  cannot leave a half-placed bracket (the ``SL_MARKET`` crash);
* a stop-loss order carries its trigger in ``stop_price``, which is the only place
  the broker reads it from;
* a duplicate intent is **not** resubmitted — a guard the retry path can bypass is
  not a guard;
* a venue call that raises leaves the order ``SUBMITTED`` and records neither a
  fill nor a rejection, because the outcome is genuinely unknown.
"""

from __future__ import annotations

from typing import Any

import pytest

from forward.appdb.identity_repository import UserRepository
from forward.appdb.order_storage import SqlOrderStorage
from forward.brokers.venue import BrokerVenue, outcome_from_broker_order
from forward.core.errors import VenueError
from forward.execution.oms import OrderDraft
from forward.execution.venue import (
    VENUE_ACCEPTED,
    VENUE_FILLED,
    VENUE_PARTIAL,
    VENUE_REJECTED,
    VenueOutcome,
)
from forward.services.execution import ExecutionService
from forward.services.orders import OrderService


@pytest.fixture()
def trader(app_db):
    with app_db.session() as session:
        return UserRepository.create(
            session, email="t@example.com", username="trader",
            password_hash="x", role="trader",
        )["user_id"]


class _RecordingVenue:
    """A venue that records what it was asked and answers with a scripted outcome."""

    def __init__(self, outcome: VenueOutcome | None = None, *, raises: Exception | None = None):
        self.calls: list[OrderDraft] = []
        self.outcome = outcome or VenueOutcome(status=VENUE_ACCEPTED, broker_order_id="BR-1")
        self.raises = raises

    def submit(self, draft: OrderDraft) -> VenueOutcome:
        self.calls.append(draft)
        if self.raises is not None:
            raise self.raises
        return self.outcome


def _draft(user_id: str, **overrides) -> OrderDraft:
    kwargs = {
        "user_id": user_id,
        "symbol": "RELIANCE",
        "side": "BUY",
        "quantity": 10,
        "mode": "PAPER",
        "limit_price": 2500.0,
        "requested_price": 2500.0,
    }
    kwargs.update(overrides)
    return OrderDraft(**kwargs)


def _service(app_db, venue) -> ExecutionService:
    from forward.execution.oms import RiskDecision

    def allow(draft: OrderDraft) -> RiskDecision:  # noqa: ARG001
        return RiskDecision.ok()

    return ExecutionService(
        orders=OrderService(SqlOrderStorage(app_db), risk_gate=allow), venue=venue
    )


# =========================================================================== order types


def test_every_accepted_spelling_resolves_to_a_real_enum_member():
    """A spelling pointing at a name the enum lacks is the same bug again."""
    from forward.core.enums import OrderType

    for spelling in ("MARKET", "LIMIT", "STOP", "STOP_LIMIT", "SL", "SL-M", "SL-L",
                     "stop_market", "sl m"):
        assert isinstance(OrderType.parse(spelling), OrderType), spelling


def test_an_unknown_order_type_is_refused_before_anything_is_sent(app_db, trader):
    venue = _RecordingVenue()
    service = _service(app_db, venue)

    with pytest.raises(ValueError, match="unknown order type"):
        service.place(_draft(trader, order_type="TELEPORT"))
    assert venue.calls == [], "an unparseable order type must not reach the venue"


# =========================================================================== happy path
def test_an_accepted_order_is_acknowledged(app_db, trader):
    venue = _RecordingVenue(VenueOutcome(status=VENUE_ACCEPTED, broker_order_id="BR-7"))
    result = _service(app_db, venue).place(_draft(trader))

    assert result.created is True
    assert result.status == "ACKNOWLEDGED"
    assert result.broker_order_id == "BR-7"
    assert len(venue.calls) == 1


def test_a_full_fill_lands_on_filled_with_its_price(app_db, trader):
    venue = _RecordingVenue(
        VenueOutcome(
            status=VENUE_FILLED, broker_order_id="BR-8",
            filled_qty=10.0, filled_price=2502.5,
        )
    )
    service = _service(app_db, venue)
    result = service.place(_draft(trader))

    assert result.status == "FILLED"
    assert result.filled_quantity == 10.0
    order = service.orders.get(result.order_id, trader)
    assert order["avg_fill_price"] == 2502.5
    assert order["completed_at"] is not None

    # And the slippage is a measurement, not a claim.
    fill = service.orders.fill_history(result.order_id, trader)[-1]
    assert fill["slippage_bps"] == pytest.approx(10.0)


def test_a_partial_fill_lands_on_partially_filled(app_db, trader):
    venue = _RecordingVenue(
        VenueOutcome(status=VENUE_PARTIAL, broker_order_id="BR-9",
                     filled_qty=4.0, filled_price=2501.0)
    )
    result = _service(app_db, venue).place(_draft(trader, quantity=10))
    assert result.status == "PARTIALLY_FILLED"
    assert result.filled_quantity == 4.0


def test_the_lifecycle_is_reconstructable_after_a_venue_round_trip(app_db, trader):
    venue = _RecordingVenue(
        VenueOutcome(status=VENUE_FILLED, broker_order_id="BR-10",
                     filled_qty=10.0, filled_price=2500.0)
    )
    service = _service(app_db, venue)
    result = service.place(_draft(trader))

    events = service.orders.history(result.order_id, trader)
    assert [(e["from_status"], e["to_status"]) for e in events] == [
        (None, "NEW"),
        ("NEW", "VALIDATING"),
        ("VALIDATING", "RISK_APPROVED"),
        ("RISK_APPROVED", "SUBMITTED"),
        ("SUBMITTED", "ACKNOWLEDGED"),
        ("ACKNOWLEDGED", "FILLED"),
    ]
    assert [e["source"] for e in events][-3:] == ["oms", "broker", "broker"]


# =========================================================================== refusals
def test_a_risk_rejection_never_reaches_the_venue(app_db, trader):
    """The whole point of the gate: a refused order must not be transmitted."""
    from forward.execution.oms import RiskDecision

    venue = _RecordingVenue()
    service = ExecutionService(
        orders=OrderService(
            SqlOrderStorage(app_db), risk_gate=lambda d: RiskDecision.reject("daily loss limit", "daily_loss")
        ),
        venue=venue,
    )
    result = service.place(_draft(trader))

    assert result.status == "REJECTED"
    assert result.reject_reason == "daily loss limit"
    assert venue.calls == []


def test_a_venue_rejection_is_recorded_with_its_reason(app_db, trader):
    venue = _RecordingVenue(
        VenueOutcome(status=VENUE_REJECTED, reject_reason="RMS: margin insufficient")
    )
    service = _service(app_db, venue)
    result = service.place(_draft(trader))

    assert result.status == "REJECTED"
    assert result.reject_reason == "RMS: margin insufficient"
    events = service.orders.history(result.order_id, trader)
    assert events[-1]["source"] == "broker"
    assert events[-1]["from_status"] == "SUBMITTED"


def test_a_rejection_without_a_reason_is_refused_at_construction():
    """A rejection whose reason is missing cannot be acted on."""
    with pytest.raises(ValueError, match="must carry a reason"):
        VenueOutcome(status=VENUE_REJECTED, reject_reason="  ")


def test_an_unknown_venue_status_is_refused_at_construction():
    with pytest.raises(ValueError, match="venue status must be one of"):
        VenueOutcome(status="MAYBE")


def test_a_venue_that_raises_leaves_the_order_submitted(app_db, trader):
    """The outcome is unknown, so the record says unknown — not filled, not rejected."""
    venue = _RecordingVenue(raises=ConnectionError("socket closed mid-request"))
    service = _service(app_db, venue)

    with pytest.raises(VenueError) as caught:
        service.place(_draft(trader))

    order_id = caught.value.order_id
    assert order_id
    assert "unknown" in str(caught.value)

    order = service.orders.get(order_id, trader)
    assert order["status"] == "SUBMITTED"
    assert order["filled_quantity"] == 0.0
    assert order["reject_reason"] is None
    assert order["completed_at"] is None

    events = service.orders.history(order_id, trader)
    assert events[-1]["to_status"] == "SUBMITTED"


def test_a_fill_with_no_price_is_refused_rather_than_recorded_at_zero(app_db, trader):
    """A zero fill price would corrupt every P&L figure downstream."""
    venue = _RecordingVenue(
        VenueOutcome(status=VENUE_FILLED, broker_order_id="BR-11",
                     filled_qty=10.0, filled_price=None)
    )
    service = _service(app_db, venue)
    with pytest.raises(ValueError, match="refusing to record a fill at zero"):
        service.place(_draft(trader))


# =========================================================================== idempotency
def test_a_duplicate_intent_is_not_resubmitted(app_db, trader):
    """A guard the retry path can bypass is not a guard."""
    venue = _RecordingVenue()
    service = _service(app_db, venue)
    key = "e" * 64

    first = service.place(_draft(trader), idempotency_key=key)
    second = service.place(_draft(trader), idempotency_key=key)

    assert first.created is True
    assert second.created is False
    assert second.duplicate_of == first.order_id
    assert second.order_id == first.order_id
    assert len(venue.calls) == 1, "the duplicate was transmitted a second time"
    assert service.orders.list_orders(trader)[1] == 1


def test_a_duplicate_returns_the_order_at_its_current_state(app_db, trader):
    venue = _RecordingVenue(
        VenueOutcome(status=VENUE_FILLED, broker_order_id="BR-12",
                     filled_qty=10.0, filled_price=2500.0)
    )
    service = _service(app_db, venue)
    key = "f" * 64
    service.place(_draft(trader), idempotency_key=key)

    again = service.place(_draft(trader), idempotency_key=key)
    assert again.status == "FILLED"
    assert again.filled_quantity == 10.0


# =========================================================================== the venue adapter
class _FakeMaster:
    def __init__(self) -> None:
        self.lookups: list[tuple[str, str]] = []

    def find(self, symbol: str, exchange: str):
        from forward.core.instrument_models import Instrument

        self.lookups.append((symbol, exchange))
        return Instrument(symbol=symbol, exchange=exchange, multiplier=1.0)


class _FakeBroker:
    def __init__(self, status: str = "PENDING") -> None:
        self.master = _FakeMaster()
        self.placed: list[Any] = []
        self.status = status

    def place_order(self, order):
        from forward.core.enums import OrderStatus

        order.status = OrderStatus(self.status)
        order.broker_order_id = "BR-99"
        order.filled_quantity = 10.0 if self.status == "FILLED" else 0.0
        order.avg_fill_price = 2502.0 if self.status == "FILLED" else 0.0
        self.placed.append(order)
        return order


def test_the_adapter_puts_the_trigger_where_the_broker_reads_it(app_db, trader):
    """``IiflBroker`` reads ``order.stop_price``; the old code set ``limit_price``.

    So even had ``SL_MARKET`` existed, the stop would have reached the exchange
    with no trigger price.
    """
    broker = _FakeBroker()
    venue = BrokerVenue(broker=broker, instruments=broker.master.find)
    venue.submit(
        _draft(trader, side="SELL", order_type="SL-M", stop_price=2450.0, limit_price=None)
    )

    placed = broker.placed[0]
    assert placed.stop_price == 2450.0
    assert placed.limit_price is None
    assert placed.order_type.value == "STOP"


def test_the_adapter_resolves_the_instrument_through_the_injected_catalog(app_db, trader):
    broker = _FakeBroker()
    BrokerVenue(broker=broker, instruments=broker.master.find).submit(
        _draft(trader, symbol="tcs", exchange="NSEEQ")
    )
    assert broker.master.lookups == [("tcs", "NSEEQ")]


def test_the_adapter_passes_the_product_through(app_db, trader):
    broker = _FakeBroker()
    BrokerVenue(broker=broker, instruments=broker.master.find).submit(
        _draft(trader, product="CNC")
    )
    assert broker.placed[0].broker_params["product"] == "CNC"


@pytest.mark.parametrize(
    ("broker_status", "expected"),
    [
        ("PENDING", VENUE_ACCEPTED),
        ("SUBMITTED", VENUE_ACCEPTED),
        ("PARTIALLY_FILLED", VENUE_PARTIAL),
        ("FILLED", VENUE_FILLED),
        ("REJECTED", VENUE_REJECTED),
        ("CANCELLED", None),
    ],
)
def test_broker_statuses_map_onto_venue_outcomes(broker_status, expected):
    from forward.core.enums import OrderStatus, Side
    from forward.core.instrument_models import Instrument
    from forward.core.order_models import Order

    order = Order(
        instrument=Instrument(symbol="X", exchange="NSEEQ"),
        side=Side.BUY,
        quantity=1,
    )
    order.status = OrderStatus(broker_status)
    order.broker_order_id = "BR-1"
    order.reject_reason = "nope" if broker_status == "REJECTED" else None
    if broker_status in ("PARTIALLY_FILLED", "FILLED"):
        order.filled_quantity = 1
        order.avg_fill_price = 100
    if expected is None:
        with pytest.raises(ValueError, match="unexpected order status"):
            outcome_from_broker_order(order)
        return
    assert outcome_from_broker_order(order).status == expected


def test_the_mapping_is_the_single_place_a_broker_answer_is_interpreted():
    """Two callers each deciding what a broker answer means is how the old bug lived."""
    import inspect

    from forward.brokers import venue

    source = inspect.getsource(venue)
    assert source.count("def outcome_from_broker_order") == 1
