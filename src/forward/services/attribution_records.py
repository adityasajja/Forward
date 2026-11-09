"""Map computed trade attribution to its stored record."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from forward.analytics.attribution import TradeAttribution
from forward.analytics.models import TradeDetails


def attribution_row(
    *,
    user_id: str,
    episode: dict[str, Any],
    trade: TradeDetails,
    attribution: TradeAttribution,
    fingerprint: str,
    computed_at: datetime,
) -> dict[str, Any]:
    """Build the database row from one computed attribution.

    Args:
        user_id: Account that owns the trade.
        episode: Closed journal row used to build the attribution.
        trade: Trade details passed to the attribution engine.
        attribution: Result returned by the pure attribution engine.
        fingerprint: Hash of the input fields used in the calculation.
        computed_at: Time when the attribution was computed.

    Returns:
        A flat row ready for the attribution storage port.
    """
    flat = attribution.flat()
    return {
        "trade_id": attribution.trade_id,
        "user_id": user_id,
        "deployment_id": episode.get("deployment_id"),
        "symbol": attribution.symbol,
        "side": attribution.side,
        "source": attribution.source,
        # Provenance comes from the journal and is copied through the engine.
        "evidence_grade": trade.evidence_grade,
        "evidence_class": trade.evidence_class,
        "simulated": attribution.simulated,
        "attribution": json.dumps(attribution.as_dict(), default=str),
        "reason_codes": ",".join(attribution.reason_codes),
        "missing_fields": json.dumps(attribution.missing_fields, default=str),
        "entry_quality": flat.get("attribution_entry_quality"),
        "execution_quality": flat.get("attribution_execution_quality"),
        "mfe_pct": attribution.excursions.mfe_pct,
        "mae_pct": attribution.excursions.mae_pct,
        "mfe_amount": attribution.excursions.mfe_amount,
        "mae_amount": attribution.excursions.mae_amount,
        "mfe_over_risk": attribution.excursions.mfe_over_risk,
        "realized_over_risk": attribution.excursions.realized_over_risk,
        "capture_efficiency_pct": attribution.exit.capture_efficiency_pct,
        "entry_slippage_bps": attribution.entry.slippage_bps,
        "exit_slippage_bps": attribution.execution.exit_slippage_bps,
        "total_slippage_bps": attribution.execution.total_slippage_bps,
        "transaction_costs": attribution.execution.transaction_costs,
        "cost_pct": attribution.execution.cost_pct,
        "signal_to_order_sec": attribution.entry.signal_to_order_sec,
        "order_to_fill_sec": attribution.entry.order_to_fill_sec,
        "holding_sec": attribution.exit.holding_sec,
        "partial_fill": bool(attribution.entry.partial),
        "fill_ratio": attribution.entry.fill_ratio,
        "sizing_method": attribution.sizing.method,
        "sizing_cap_reason": attribution.sizing.cap_reason,
        "realized_risk_pct": attribution.risk.realized_risk_pct,
        "planned_risk_amount": attribution.risk.planned_risk_amount,
        "context_score": attribution.context.context_score,
        "context_class": attribution.context.context_class,
        "market_regime": attribution.context.market_regime,
        "sector": attribution.context.sector,
        "sector_strength": attribution.context.sector_strength,
        "stock_relative_strength": attribution.context.stock_relative_strength,
        "rvol": attribution.context.rvol,
        "atr_pct": attribution.context.atr_pct,
        "exit_reason": attribution.exit.reason,
        "input_fingerprint": fingerprint,
        "computed_at": computed_at,
        "updated_at": computed_at,
    }
