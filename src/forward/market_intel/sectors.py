"""Sector metrics derived from point-in-time stock measurements."""

from __future__ import annotations

from typing import Any

import numpy as np

from forward.market_intel.models import RegimeModelConfig, SectorMetrics


def aggregate_sectors(
    sector_stocks: dict[str, list[dict[str, Any]]],
    *,
    nifty_1d_pct: float,
    nifty_1m_pct: float,
    config: RegimeModelConfig,
) -> tuple[list[SectorMetrics], int]:
    """Build sector metrics from already measured stocks.

    Args:
        sector_stocks: Stock measurements grouped by sector.
        nifty_1d_pct: Benchmark one-day return.
        nifty_1m_pct: Benchmark monthly return.
        config: Active sector thresholds.

    Returns:
        Sector metrics sorted by monthly relative strength, and the count of
        sectors with a positive one-day return.
    """
    sector_metrics: list[SectorMetrics] = []
    positive_sectors = 0

    for sector_name, items in sector_stocks.items():
        if not items:
            continue
        count = len(items)
        sector_1d = float(np.mean([stock["change_1d_pct"] for stock in items]))
        sector_20d = float(np.mean([stock["return_20d_pct"] for stock in items]))

        if sector_1d > 0:
            positive_sectors += 1

        relative_1d = sector_1d - nifty_1d_pct
        relative_1m = sector_20d - nifty_1m_pct

        advancing = sum(
            1
            for stock in items
            if stock["change_1d_pct"] > config.daily_move_threshold_pct
        )
        declining = sum(
            1
            for stock in items
            if stock["change_1d_pct"] < -config.daily_move_threshold_pct
        )
        advance_decline_ratio = round(advancing / declining, 2) if declining else None

        total_volume = sum(stock["volume"] for stock in items)
        total_prior_volume = sum(stock["prior_vol_mean"] for stock in items)
        volume_multiple = round(total_volume / total_prior_volume, 2)

        breakout_count = sum(1 for stock in items if stock["is_breakout"])
        above_ema20_count = sum(1 for stock in items if stock["above_ema20"])
        above_ema50_count = sum(1 for stock in items if stock["above_ema50"])

        above_ema50_pct = round((above_ema50_count / count) * 100.0, 1)
        if above_ema50_pct >= config.sector_bull_breadth_pct and relative_1m > 0:
            trend = "BULLISH"
        elif above_ema50_pct <= config.sector_bear_breadth_pct and relative_1m < 0:
            trend = "BEARISH"
        else:
            trend = "SIDEWAYS"

        top_stocks = sorted(
            items, key=lambda stock: stock["change_1d_pct"], reverse=True
        )
        sector_metrics.append(
            SectorMetrics(
                sector=sector_name,
                stock_count=count,
                return_1d_pct=round(sector_1d, 2),
                return_1w_pct=round(
                    float(np.mean([stock["return_5d_pct"] for stock in items])), 2
                ),
                return_1m_pct=round(sector_20d, 2),
                relative_strength_1d=round(relative_1d, 2),
                relative_strength_1m=round(relative_1m, 2),
                advancing_count=advancing,
                declining_count=declining,
                advance_decline_ratio=advance_decline_ratio,
                volume_multiple=volume_multiple,
                breakout_count=breakout_count,
                above_ema20_count=above_ema20_count,
                above_ema20_pct=round((above_ema20_count / count) * 100.0, 1),
                above_ema50_count=above_ema50_count,
                above_ema50_pct=above_ema50_pct,
                trend=trend,
                top_stocks=top_stocks[:5],
            )
        )

    sector_metrics.sort(key=lambda sector: sector.relative_strength_1m, reverse=True)
    return sector_metrics, positive_sectors
