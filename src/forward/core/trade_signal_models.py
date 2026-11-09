"""Domain models for queued trade signals."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from forward.core.session import IST


class TradeSignalSettings(BaseModel):
    """Rules used to size and place one trade signal.

    Attributes:
        capital: Account value used for signal risk sizing.
        risk_per_trade_pct: Share of capital risked on one signal.
        stop_method: Method used to calculate the stop distance.
        stop_atr_mult: ATR multiple used by the ATR stop method.
        stop_pct: Price percentage used by the fixed percentage stop method.
        rr_ratio: Target distance as a multiple of stop distance.
        max_active: Maximum number of active signals.
        exchange: Exchange used for the signal instrument.
        product: Broker product used when placing signal orders.
    """

    capital: float = 500_000.0
    risk_per_trade_pct: float = 1.0
    stop_method: Literal["atr", "pct"] = "atr"
    stop_atr_mult: float = 1.5
    stop_pct: float = 2.0
    rr_ratio: float = 2.0
    max_active: int = 5
    exchange: str = "NSEEQ"
    product: str = "CNC"


class TradeSignal(BaseModel):
    """A trade idea awaiting approval or tracking its placed orders.

    Attributes:
        id: Short identifier for this signal.
        symbol: Market symbol for the proposed trade.
        action: Direction of the proposed trade.
        setup: Rule or research setup that created the signal.
        reason: Short explanation for the proposed trade.
        entry_price: Reference entry price.
        stop_loss: Price that invalidates the trade.
        target: Price that closes the trade with a gain.
        quantity: Number of shares in the proposed trade.
        risk_amount: Estimated loss at the stop price.
        rr_ratio: Reward-to-risk ratio at the target price.
        rsi: RSI value when the signal was created.
        vol_x: Volume multiple when the signal was created.
        setup_hit_rate_pct: Historical target hit rate for this setup.
        setup_median_days_to_target: Historical median time to target.
        paper_citation: Research source for the signal.
        thesis: Research explanation for the signal.
        confidence_score: Confidence assigned by the research system.
        expected_value: Expected value assigned by the research system.
        regime_fit: Market regime used to qualify the research signal.
        status: Current approval and execution state.
        created_at: Time when the signal was created.
        expires_at: Time after which an unapproved signal expires.
        executed_at: Time when the signal was approved.
        entry_order_id: Broker ID for the entry order.
        sl_order_id: Broker ID for the stop order.
        target_order_id: Broker ID for the target order.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    symbol: str
    action: Literal["BUY", "SELL"]
    setup: str
    reason: str
    entry_price: float
    stop_loss: float
    target: float
    quantity: int
    risk_amount: float
    rr_ratio: float
    rsi: float | None = None
    vol_x: float | None = None
    setup_hit_rate_pct: float | None = None
    setup_median_days_to_target: float | None = None
    paper_citation: str | None = None
    thesis: str | None = None
    confidence_score: float | None = None
    expected_value: float | None = None
    regime_fit: str | None = None
    status: Literal["PENDING", "ACTIVE", "DONE", "SKIPPED"] = "PENDING"
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=IST))
    expires_at: datetime | None = None
    executed_at: datetime | None = None
    entry_order_id: str | None = None
    sl_order_id: str | None = None
    target_order_id: str | None = None

    def is_expired(self) -> bool:
        """Return whether this signal has passed its expiry time."""
        if self.expires_at is None:
            return False
        return datetime.now(tz=IST) > self.expires_at
