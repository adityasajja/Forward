"""Validated settings for the morning market briefing."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from forward.core.market_universe import DEFAULT_SCAN_UNIVERSE


class BriefingConfig(BaseModel):
    """Rules that control market filtering and briefing delivery."""

    top_n: int = 8
    avoid_n: int = 5
    min_price: float = 50.0
    min_day_value_lakh: float = 50.0
    min_atr_pct: float = 0.5
    min_bars: int = 60
    universe: Literal["all", "watchlist"] = "all"
    watchlist: list[str] = Field(default_factory=lambda: list(DEFAULT_SCAN_UNIVERSE))
    ranking: Literal["vs_high", "score"] = "vs_high"
    send_enabled: bool = True
