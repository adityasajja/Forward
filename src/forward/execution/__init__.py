"""Execution and risk management.

This package is a compute layer: it defines the order state machine
(:mod:`forward.execution.oms`) and the pre-trade risk controls
(:mod:`forward.execution.risk`), and it may not import ``forward.appdb``. The
transaction-owning use case that persists transitions is
:class:`forward.services.orders.OrderService`.
"""

from forward.execution.oms import (
    EVENT_SOURCES,
    ORDER_STATES,
    TERMINAL_STATES,
    VALID_TRANSITIONS,
    InvalidTransition,
    LimitsRiskGate,
    OrderDraft,
    RiskDecision,
    RiskGate,
    assert_transition,
    can_transition,
    elapsed_ms,
    fill_status,
    is_terminal,
    next_states,
    slippage_bps,
)
from forward.execution.risk import RiskEngine, RiskLimits, RiskVerdict

__all__ = [
    "EVENT_SOURCES",
    "ORDER_STATES",
    "TERMINAL_STATES",
    "VALID_TRANSITIONS",
    "InvalidTransition",
    "LimitsRiskGate",
    "OrderDraft",
    "RiskDecision",
    "RiskEngine",
    "RiskGate",
    "RiskLimits",
    "RiskVerdict",
    "assert_transition",
    "can_transition",
    "elapsed_ms",
    "fill_status",
    "is_terminal",
    "next_states",
    "slippage_bps",
]
