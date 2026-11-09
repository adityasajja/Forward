"""Configuration for automatic stock alerts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class IntelligentAlertConfig(BaseModel):
    """Rules and timing for automated buy and sell alerts.

    Attributes:
        enabled: Whether automatic alerts are active.
        universe: Source of symbols checked for alerts.
        interval_min: Minutes between alert scans.
        cooldown_min: Minutes before the same setup can alert again.
        sell_sma_breakdown: Whether an SMA breakdown can trigger a sell alert.
        sell_rsi_overbought: Whether an overbought RSI can trigger a sell alert.
        sell_rsi_threshold: RSI level that counts as overbought.
        sell_take_profit_enabled: Whether take-profit alerts are active.
        sell_take_profit_pct: Gain percentage for a take-profit alert.
        sell_stop_loss_enabled: Whether stop-loss alerts are active.
        sell_stop_loss_pct: Loss percentage for a stop-loss alert.
        sell_trailing_stop_enabled: Whether trailing-stop alerts are active.
        sell_trailing_stop_pct: Pullback percentage for a trailing stop.
        sell_composite_min_score: Minimum combined score for a sell alert.
        sell_stall_enabled: Whether stalled-position alerts are active.
        sell_stall_days: Sessions without progress before a stall alert.
        sell_stall_atr_mult: ATR multiple used to measure stalled movement.
        sell_stall_min_gain_pct: Minimum open gain required for a stall alert.
        buy_golden_cross: Whether a golden cross can trigger a buy alert.
        buy_rsi_oversold: Whether oversold RSI can trigger a buy alert.
        buy_rsi_threshold: RSI level that counts as oversold.
        buy_breakout_vol: Whether a volume breakout can trigger a buy alert.
        buy_dip_sma20: Whether a dip near SMA20 can trigger a buy alert.
        buy_box_oscillation: Whether range-bound box setups are enabled.
        buy_box_lookback: Sessions used to measure the price box.
        buy_box_width_pct: Maximum box width as a share of price.
        buy_box_floor_pct: Minimum distance from the box floor as a share of price.
    """

    enabled: bool = True
    universe: Literal["holdings", "watchlist", "both"] = "both"
    interval_min: int = 3
    cooldown_min: int = 45
    sell_sma_breakdown: bool = True
    sell_rsi_overbought: bool = True
    sell_rsi_threshold: float = 75.0
    sell_take_profit_enabled: bool = True
    sell_take_profit_pct: float = 8.0
    sell_stop_loss_enabled: bool = True
    sell_stop_loss_pct: float = 4.0
    sell_trailing_stop_enabled: bool = True
    sell_trailing_stop_pct: float = 3.0
    sell_composite_min_score: float = 2.0
    sell_stall_enabled: bool = False
    sell_stall_days: int = 10
    sell_stall_atr_mult: float = 1.5
    sell_stall_min_gain_pct: float = 5.0
    buy_golden_cross: bool = True
    buy_rsi_oversold: bool = True
    buy_rsi_threshold: float = 32.0
    buy_breakout_vol: bool = True
    buy_dip_sma20: bool = True
    buy_box_oscillation: bool = False
    buy_box_lookback: int = 20
    buy_box_width_pct: float = 8.0
    buy_box_floor_pct: float = 2.0
