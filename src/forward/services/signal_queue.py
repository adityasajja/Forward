"""Store and update the pending and active trade signal queue."""

from __future__ import annotations

from datetime import datetime

from forward.core.session import IST
from forward.core.trade_signal_models import TradeSignal
from forward.core.trade_signal_ports import TradeSignalStoragePort


class SignalQueue:
    """Keep trade signals in memory and persist each state change."""

    def __init__(self, storage: TradeSignalStoragePort) -> None:
        """Create the queue and load its saved signals.

        Args:
            storage: Adapter that persists queue records.
        """
        self._storage = storage
        self._signals: dict[str, TradeSignal] = {}
        self._load()

    def _load(self) -> None:
        """Load and validate every saved signal."""
        self._signals = {
            signal.id: signal
            for signal in (
                TradeSignal.model_validate(record) for record in self._storage.load_queue()
            )
        }

    def _commit(self, signals: dict[str, TradeSignal]) -> None:
        """Save a queue update before making it visible in memory."""
        records = [signal.model_dump(mode="json") for signal in signals.values()]
        self._storage.save_queue(records)
        self._signals = signals

    def add(self, signal: TradeSignal) -> None:
        """Add a signal and skip older pending signals for the same action.

        Args:
            signal: Signal to add to the queue.
        """
        updated = dict(self._signals)
        for signal_id, existing in self._signals.items():
            if (
                existing.symbol == signal.symbol
                and existing.action == signal.action
                and existing.status == "PENDING"
            ):
                updated[signal_id] = existing.model_copy(update={"status": "SKIPPED"})
        updated[signal.id] = signal
        self._commit(updated)

    def get(self, signal_id: str) -> TradeSignal | None:
        """Return one signal by ID.

        Args:
            signal_id: ID to find.

        Returns:
            The signal, or ``None`` when it is not in the queue.
        """
        return self._signals.get(signal_id)

    def all(self) -> list[TradeSignal]:
        """Return all signals newest first and expire stale pending signals."""
        updated = dict(self._signals)
        changed = False
        for signal_id, signal in self._signals.items():
            if signal.status == "PENDING" and signal.is_expired():
                updated[signal_id] = signal.model_copy(update={"status": "SKIPPED"})
                changed = True
        if changed:
            self._commit(updated)
        return sorted(self._signals.values(), key=lambda signal: signal.created_at, reverse=True)

    def pending(self) -> list[TradeSignal]:
        """Return signals that are waiting for approval."""
        return [signal for signal in self.all() if signal.status == "PENDING"]

    def active(self) -> list[TradeSignal]:
        """Return signals with executed orders."""
        return [signal for signal in self.all() if signal.status == "ACTIVE"]

    def skip(self, signal_id: str) -> bool:
        """Mark a signal as skipped.

        Args:
            signal_id: ID to skip.

        Returns:
            Whether a signal with the ID was found.
        """
        signal = self._signals.get(signal_id)
        if signal is None:
            return False
        updated = dict(self._signals)
        updated[signal_id] = signal.model_copy(update={"status": "SKIPPED"})
        self._commit(updated)
        return True

    def mark_active(self, signal_id: str, entry_id: str, stop_id: str, target_id: str) -> None:
        """Record the orders that execute a signal.

        Args:
            signal_id: Signal that was executed.
            entry_id: Broker ID for the entry order.
            stop_id: Broker ID for the stop order.
            target_id: Broker ID for the target order.
        """
        signal = self._signals.get(signal_id)
        if signal is None:
            return
        updated = dict(self._signals)
        updated[signal_id] = signal.model_copy(
            update={
                "status": "ACTIVE",
                "executed_at": datetime.now(tz=IST),
                "entry_order_id": entry_id,
                "sl_order_id": stop_id,
                "target_order_id": target_id,
            }
        )
        self._commit(updated)

    def active_count(self) -> int:
        """Return the number of active signals."""
        return len(self.active())
