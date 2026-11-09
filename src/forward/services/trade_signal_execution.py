"""Place and record the orders that execute one trade signal."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any

from forward.core.alert_ports import AlertDeliveryPort
from forward.core.errors import VenueError
from forward.core.trade_signal_models import TradeSignal
from forward.core.trade_signal_ports import (
    SignalExecutionAuditPort,
    TradeSignalQueuePort,
)
from forward.execution.oms import OrderDraft, idempotency_key_for
from forward.services.execution import ExecutionService

logger = logging.getLogger(__name__)

SignalFormatter = Callable[[TradeSignal], tuple[str, str]]
ChannelFactory = Callable[[], Sequence[AlertDeliveryPort]]


class SignalNotFoundError(LookupError):
    """The requested signal does not exist."""


class SignalNotPendingError(ValueError):
    """The requested signal is not waiting for approval."""


class SignalOrderValidationError(ValueError):
    """The execution service rejected one signal order before submission."""


class SignalVenueFailure(RuntimeError):
    """A venue call failed after an order may have been submitted."""

    def __init__(self, message: str, placed: dict[str, dict[str, Any]]) -> None:
        """Create the error with orders that were placed before the failure.

        Args:
            message: Venue failure and leg information for the operator.
            placed: Results for earlier legs in this bracket.
        """
        self.placed = placed
        super().__init__(message)


class TradeSignalExecutionService:
    """Approve a queued signal through the shared order lifecycle."""

    def __init__(
        self,
        *,
        queue: TradeSignalQueuePort,
        audit: SignalExecutionAuditPort,
        channel_factory: ChannelFactory,
        format_confirmation: SignalFormatter,
    ) -> None:
        """Create the signal execution workflow.

        Args:
            queue: Store for pending and active signals.
            audit: Writer for order approval events.
            channel_factory: Creates the configured confirmation channels.
            format_confirmation: Formats the completed bracket for delivery.
        """
        self._queue = queue
        self._audit = audit
        self._channel_factory = channel_factory
        self._format_confirmation = format_confirmation

    def get_pending_signal(self, signal_id: str) -> TradeSignal:
        """Return a signal that is waiting for approval.

        Args:
            signal_id: Signal identifier from the request path.

        Returns:
            The pending signal.

        Raises:
            SignalNotFoundError: If the queue has no matching signal.
            SignalNotPendingError: If the signal has already changed state.
        """
        signal = self._queue.get(signal_id)
        if signal is None:
            raise SignalNotFoundError(f"Signal {signal_id} not found")
        if signal.status != "PENDING":
            raise SignalNotPendingError(f"Signal is {signal.status}, not PENDING")
        return signal

    def execute(
        self,
        *,
        signal_id: str,
        signal: TradeSignal,
        user_id: str,
        username: str,
        execution: ExecutionService,
    ) -> dict[str, Any]:
        """Place the entry, stop, and target orders for one signal.

        Args:
            signal_id: Identifier of the approved signal.
            signal: Signal checked by :meth:`get_pending_signal`.
            user_id: Account that owns the orders.
            username: Account name recorded in the audit trail.
            execution: Shared order lifecycle and live venue.

        Returns:
            Broker order IDs and the result for each bracket leg.

        Raises:
            SignalOrderValidationError: If the order lifecycle rejects a leg.
            SignalVenueFailure: If a broker call fails after earlier legs placed.
        """
        entry_side = "BUY" if signal.action == "BUY" else "SELL"
        exit_side = "SELL" if signal.action == "BUY" else "BUY"
        version = getattr(signal, "strategy_version", None)
        strategy_id = getattr(signal, "strategy_id", None)
        legs = (
            (0, entry_side, "MARKET", None, None, "FORWARD-SEMI"),
            (1, exit_side, "SL-M", None, float(signal.stop_loss), "FORWARD-SL"),
            (2, exit_side, "LIMIT", float(signal.target), None, "FORWARD-TGT"),
        )

        placed: dict[str, dict[str, Any]] = {}
        for leg_index, side, order_type, limit_price, stop_price, tag in legs:
            draft = OrderDraft(
                user_id=user_id,
                symbol=signal.symbol,
                side=side,
                quantity=float(signal.quantity),
                mode="LIVE",
                exchange="NSEEQ",
                order_type=order_type,
                limit_price=limit_price,
                stop_price=stop_price,
                product="CNC",
                requested_price=float(signal.entry_price) if signal.entry_price else None,
                signal_id=signal_id,
                strategy_id=strategy_id,
                strategy_version=version,
                tag=tag,
            )
            key = idempotency_key_for(
                user_id=user_id,
                strategy_id=strategy_id,
                strategy_version=version,
                signal_id=signal_id,
                symbol=signal.symbol,
                side=side,
                leg_index=leg_index,
            )
            try:
                result = execution.place(draft, idempotency_key=key)
            except ValueError as error:
                raise SignalOrderValidationError(f"leg {leg_index} ({tag}): {error}") from error
            except VenueError as error:
                raise SignalVenueFailure(
                    f"leg {leg_index} ({tag}) failed: {error}", placed
                ) from error
            placed[tag] = {
                "order_id": result.order_id,
                "status": result.status,
                "broker_order_id": result.broker_order_id,
                "reject_reason": result.reject_reason,
                "duplicate": result.duplicate,
            }

        self._audit.append(
            actor=username,
            action="signal.execute",
            subject=f"{signal.action} {signal.quantity} {signal.symbol}",
            detail=f"signal {signal_id} approved from the trade queue",
        )
        self._queue.mark_active(
            signal_id,
            placed["FORWARD-SEMI"]["broker_order_id"] or "",
            placed["FORWARD-SL"]["broker_order_id"] or "",
            placed["FORWARD-TGT"]["broker_order_id"] or "",
        )
        self._send_confirmation(signal_id, signal)

        return {
            "signal_id": signal_id,
            "entry_order": placed["FORWARD-SEMI"]["broker_order_id"],
            "sl_order": placed["FORWARD-SL"]["broker_order_id"],
            "target_order": placed["FORWARD-TGT"]["broker_order_id"],
            "orders": placed,
        }

    def _send_confirmation(self, signal_id: str, signal: TradeSignal) -> None:
        """Send a confirmation after the bracket is recorded as active.

        Args:
            signal_id: Signal that was executed.
            signal: Signal details shown in the confirmation.
        """
        channels = self._channel_factory()
        header, body = self._format_confirmation(signal)
        for channel in channels:
            try:
                if channel.send(header, body):
                    break
            except Exception:  # noqa: BLE001 - a notification must not undo a placement
                logger.debug("telegram confirm failed for signal %s", signal_id)
