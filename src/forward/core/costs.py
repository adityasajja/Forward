"""Transaction cost models.

Underestimating costs is the single most common reason a backtest looks
profitable and live trading doesn't. Both models are explicit and pluggable.
"""

from __future__ import annotations

from dataclasses import dataclass

from forward.core.enums import Side
from forward.core.instrument_models import Instrument


@dataclass
class CommissionModel:
    """IBKR-style tiered/fixed commission approximation.

    ``per_share`` for equities, ``per_contract`` for F&O. Whichever is
    relevant for the instrument's asset class is used; the other is ignored.

    For NSE cash equity this model is **badly wrong** and should not be used:
    it charges Rs 0.005/share with no ad-valorem component, while the real bill
    is dominated by Securities Transaction Tax at 0.1% of turnover on *both*
    sides. See :class:`IndianDeliveryCosts`.
    """

    per_share: float
    per_contract: float
    min_per_order: float
    max_pct_of_trade: float  # IBKR caps commissions as % of trade value
    pct_of_notional: float  # exchange/clearing fees, in decimal

    def compute(
        self,
        quantity: float,
        price: float,
        instrument: Instrument,
        side: Side,
    ) -> float:
        """Calculate trading fees for one order.

        Args:
            quantity: Number of shares or contracts.
            price: Price per share or contract.
            instrument: Instrument being traded.
            side: Trade direction, required by asymmetric fee models.

        Returns:
            Total fee in the account currency.
        """
        notional = abs(quantity * price * instrument.multiplier)
        if instrument.is_derivative:
            base = self.per_contract * quantity
        else:
            base = self.per_share * quantity
        pct_fee = self.pct_of_notional * notional
        commission = max(base + pct_fee, self.min_per_order)
        # Regulatory-style cap
        return min(commission, self.max_pct_of_trade * notional) if notional > 0 else commission


@dataclass
class IndianDeliveryCosts(CommissionModel):
    """Realistic NSE cash-equity **delivery** costs, 2026 rates.

    Why this exists: every strategy measured in this repo before it was costed
    with the IBKR-style default, which charges Rs 0.005/share and no statutory
    levy at all. A delivery round trip in India actually costs roughly **0.25% of
    turnover**, and almost all of that is ad valorem:

    ====================  =========  ===================================
    Charge                Rate       Notes
    ====================  =========  ===================================
    STT                   0.1%       **each side** — the dominant cost
    Stamp duty            0.015%     buy only
    NSE transaction       0.00297%   each side
    SEBI turnover fee     Rs 10/cr   each side
    GST                   18%        on brokerage + exchange + SEBI
    Brokerage             Rs 20/ord  or 0.03%, whichever is lower
    DP charges            Rs 13.5    per sell, per scrip
    ====================  =========  ===================================

    The consequence for strategy selection is not a rounding error. A rule that
    trades 1,900 times pays ~19% of the account in frictions over a 1,000-session
    out-of-sample window; a rule that rebalances 20 times pays ~0.2%. That
    difference is larger than most of the edges anyone was chasing, which is the
    single most useful thing a cost model can tell you.

    Not modelled: short-term vs long-term capital gains. Converting a long-term
    holding into many short-term ones costs a further 7.5 points of tax on gains
    (20% STCG vs 12.5% LTCG), so the real bar is higher still.
    """

    brokerage_per_order: float
    brokerage_pct: float
    #: Securities Transaction Tax. 0.1% on both legs for delivery.
    stt_pct: float
    #: Stamp duty, charged on the buy leg only.
    stamp_pct_buy: float
    exchange_pct: float
    sebi_pct: float
    gst_pct: float
    dp_per_sell: float

    def _trading_charges(self, notional: float) -> float:
        """Calculate brokerage, exchange, SEBI, and GST charges.

        Args:
            notional: Absolute value of the trade in rupees.

        Returns:
            The charges that both delivery and intraday trades share.
        """
        brokerage = min(self.brokerage_per_order, self.brokerage_pct * notional)
        exchange = self.exchange_pct * notional
        sebi = self.sebi_pct * notional
        return brokerage + exchange + sebi + self.gst_pct * (brokerage + exchange + sebi)

    def compute(
        self,
        quantity: float,
        price: float,
        instrument: Instrument,
        side: Side,
    ) -> float:
        notional = abs(quantity * price * instrument.multiplier)
        if notional <= 0:
            return 0.0

        total = self._trading_charges(notional)
        total += self.stt_pct * notional
        if side is Side.SELL:
            total += self.dp_per_sell
        elif side is Side.BUY:
            total += self.stamp_pct_buy * notional
        else:
            raise ValueError(f"unsupported trade side: {side!r}")
        return total


@dataclass
class IndianIntradayCosts(IndianDeliveryCosts):
    """NSE cash-equity **intraday** (MIS) costs. Differs from delivery in three places:
    STT is 0.025% on the **sell** leg only, stamp duty is 0.003% on the buy leg, and there is
    no DP charge (nothing is delivered to a demat account)."""

    stt_pct: float
    stamp_pct_buy: float
    dp_per_sell: float

    def compute(
        self, quantity: float, price: float, instrument: Instrument, side: Side
    ) -> float:
        notional = abs(quantity * price * instrument.multiplier)
        if notional <= 0:
            return 0.0
        total = self._trading_charges(notional)
        if side is Side.SELL:
            total += self.stt_pct * notional
        else:
            total += self.stamp_pct_buy * notional
        return total


@dataclass
class SlippageModel:
    """Adverse price movement between signal and fill.

    Applied in the direction that hurts: buys fill higher, sells fill lower.
    Either a fixed basis-point cost or a number of ticks.
    """

    bps: float
    ticks: float


    def apply(self, price: float, side: Side, instrument: Instrument) -> float:
        factor = self.bps / 10_000.0
        tick_cost = self.ticks * instrument.tick_size
        adjustment = price * factor + tick_cost
        return price + (adjustment if side is Side.BUY else -adjustment)
