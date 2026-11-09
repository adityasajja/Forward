"""Build explicit transaction-cost models from required application settings."""

from forward.config.settings import get_settings
from forward.core.costs import (
    CommissionModel,
    IndianDeliveryCosts,
    IndianIntradayCosts,
    SlippageModel,
)


def commission_model(**overrides: float) -> CommissionModel:
    """Return the configured flat commission model."""
    settings = get_settings()
    values = {
        "per_share": settings.commission_model_per_share,
        "per_contract": settings.commission_model_per_contract,
        "min_per_order": settings.commission_model_min_per_order,
        "max_pct_of_trade": settings.commission_model_max_pct_of_trade,
        "pct_of_notional": settings.commission_model_pct_of_notional,
    }
    values.update(overrides)
    return CommissionModel(**values)


def indian_delivery_costs() -> IndianDeliveryCosts:
    """Return configured Indian delivery charges."""
    settings = get_settings()
    return IndianDeliveryCosts(
        per_share=settings.commission_model_per_share,
        per_contract=settings.commission_model_per_contract,
        min_per_order=settings.commission_model_min_per_order,
        max_pct_of_trade=settings.commission_model_max_pct_of_trade,
        pct_of_notional=settings.commission_model_pct_of_notional,
        brokerage_per_order=settings.indian_brokerage_per_order,
        brokerage_pct=settings.indian_brokerage_pct,
        stt_pct=settings.indian_delivery_stt_pct,
        stamp_pct_buy=settings.indian_delivery_stamp_pct_buy,
        exchange_pct=settings.indian_exchange_pct,
        sebi_pct=settings.indian_sebi_pct,
        gst_pct=settings.indian_gst_pct,
        dp_per_sell=settings.indian_delivery_dp_per_sell,
    )


def indian_intraday_costs() -> IndianIntradayCosts:
    """Return configured Indian intraday charges."""
    settings = get_settings()
    return IndianIntradayCosts(
        per_share=settings.commission_model_per_share,
        per_contract=settings.commission_model_per_contract,
        min_per_order=settings.commission_model_min_per_order,
        max_pct_of_trade=settings.commission_model_max_pct_of_trade,
        pct_of_notional=settings.commission_model_pct_of_notional,
        brokerage_per_order=settings.indian_brokerage_per_order,
        brokerage_pct=settings.indian_brokerage_pct,
        stt_pct=settings.indian_intraday_stt_pct_sell,
        stamp_pct_buy=settings.indian_intraday_stamp_pct_buy,
        exchange_pct=settings.indian_exchange_pct,
        sebi_pct=settings.indian_sebi_pct,
        gst_pct=settings.indian_gst_pct,
        dp_per_sell=settings.indian_intraday_dp_per_sell,
    )


def slippage_model(*, bps: float | None = None, ticks: float | None = None) -> SlippageModel:
    """Return the configured slippage model, with explicit per-run overrides."""
    settings = get_settings()
    return SlippageModel(
        bps=settings.slippage_default_bps if bps is None else bps,
        ticks=settings.slippage_default_ticks if ticks is None else ticks,
    )
