"""Project stored attribution measurements into learning dataset columns."""

from __future__ import annotations

from typing import Any


def attribution_columns(record: dict[str, Any]) -> dict[str, Any]:
    """Map one stored attribution record to its dataset columns.

    Values pass through unchanged, including None. Missing measurements stay
    missing so they cannot be mistaken for a zero.

    Args:
        record: Stored attribution measurements for one trade.

    Returns:
        Attribution values keyed by learning dataset column name.
    """
    return {
        "attribution_entry_quality": record.get("entry_quality"),
        "attribution_execution_quality": record.get("execution_quality"),
        "attribution_exit_quality": record.get("capture_efficiency_pct"),
        "attribution_entry_slippage_bps": record.get("entry_slippage_bps"),
        "attribution_exit_slippage_bps": record.get("exit_slippage_bps"),
        "attribution_total_slippage_bps": record.get("total_slippage_bps"),
        "attribution_transaction_costs": record.get("transaction_costs"),
        "attribution_cost_pct": record.get("cost_pct"),
        "attribution_signal_to_order_sec": record.get("signal_to_order_sec"),
        "attribution_order_to_fill_sec": record.get("order_to_fill_sec"),
        "attribution_holding_sec": record.get("holding_sec"),
        "attribution_partial_fill": record.get("partial_fill"),
        "attribution_fill_ratio": record.get("fill_ratio"),
        "attribution_sizing_method": record.get("sizing_method"),
        "attribution_sizing_cap_reason": record.get("sizing_cap_reason"),
        "attribution_realized_risk_pct": record.get("realized_risk_pct"),
        "attribution_planned_risk_amount": record.get("planned_risk_amount"),
        "attribution_mfe_over_risk": record.get("mfe_over_risk"),
        "attribution_realized_over_risk": record.get("realized_over_risk"),
        "attribution_capture_efficiency_pct": record.get("capture_efficiency_pct"),
        "attribution_context_score": record.get("context_score"),
        "attribution_context_class": record.get("context_class"),
        "attribution_sector_strength": record.get("sector_strength"),
        "attribution_stock_relative_strength": record.get("stock_relative_strength"),
        "attribution_rvol": record.get("rvol"),
        "attribution_atr_pct": record.get("atr_pct"),
        "attribution_market_regime": record.get("market_regime"),
        "attribution_reason_codes": ",".join(record.get("reason_codes") or []),
    }
