"""Indian Equity Market Intelligence service.

Provides a unified, point-in-time and current-snapshot market intelligence layer:
MARKET → SECTOR → STOCK → STRATEGY → TRADE → LEARNING

Strictly focused on Indian Cash Equities (NSE/BSE). No F&O or options data.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from forward.core.errors import InvalidMarketIntelSort, MarketHistoryUnavailable
from forward.core.market_intel_ports import (
    MarketIntelIndexRefreshPort,
    MarketIntelMarketDataPort,
    MarketIntelSnapshotCachePort,
)
from forward.core.symbols import canonical_symbol
from forward.market_intel.models import (
    DEFAULT_REGIME_CONFIG_V1,
    BenchmarkProvenance,
    MarketSummary,
    RawMarketMeasurements,
    RegimeModelConfig,
    SectorMetrics,
    StockContext,
)
from forward.market_intel.regime import classify_regime_from_raw
from forward.market_intel.sectors import aggregate_sectors
from forward.strategy.indicators import atr as calc_atr
from forward.strategy.indicators import ema as calc_ema
from forward.strategy.indicators import sma as calc_sma


class MarketIntelService:
    """Computes real-time and point-in-time intelligence for Indian cash equities."""

    def __init__(
        self,
        market_data: MarketIntelMarketDataPort,
        index_refresh: MarketIntelIndexRefreshPort,
        snapshot_cache: MarketIntelSnapshotCachePort,
        regime_config: RegimeModelConfig | None = None,
    ) -> None:
        """Create the market-intelligence use case with explicit data access.

        Args:
            market_data: Port that reads local market history.
            index_refresh: Port that refreshes official index history.
            snapshot_cache: Port used to load and save computed snapshots.
            regime_config: Versioned thresholds used to classify the market.
        """
        self._market_data = market_data
        self._index_refresh = index_refresh
        self._snapshot_cache = snapshot_cache
        self._lock = threading.Lock()
        # Held for the whole computation; `_lock` only guards the cache fields.
        self._compute_lock = threading.Lock()
        self.regime_config = (
            regime_config if regime_config is not None else DEFAULT_REGIME_CONFIG_V1
        )
        self._cached_summary: MarketSummary | None = None
        self._cached_sectors: list[SectorMetrics] | None = None
        self._cached_stocks: dict[str, StockContext] | None = None
        self._cache_time: float = 0.0
        self._ttl_seconds: float = 300.0

    # --------------------------------------------------------------------------
    # Configuration & Versioning
    # --------------------------------------------------------------------------
    def get_regime_config(self) -> RegimeModelConfig:
        with self._lock:
            return self.regime_config

    def regime_config_payload(self) -> dict[str, Any]:
        """Return the active regime model settings as a JSON-ready mapping."""
        with self._lock:
            return asdict(self.regime_config)

    def ranked_sectors(
        self,
        *,
        sort_by: str,
        descending: bool = True,
        force_refresh: bool = False,
    ) -> list[SectorMetrics]:
        """Return sector results sorted by a supported measurement.

        Args:
            sort_by: Sector measurement used for ordering.
            descending: Whether higher values appear first.
            force_refresh: Whether to bypass the cached market snapshot.

        Returns:
            Sector measurements in the requested order.

        Raises:
            InvalidMarketIntelSort: If ``sort_by`` is not supported.
        """
        fields = (
            "relative_strength_1d",
            "relative_strength_1m",
            "return_1d_pct",
            "return_1w_pct",
            "return_1m_pct",
            "volume_multiple",
            "advance_decline_ratio",
            "above_ema50_pct",
            "breakout_count",
        )
        if sort_by not in fields:
            raise InvalidMarketIntelSort(
                f"sort_by must be one of: {', '.join(sorted(fields))}"
            )
        _summary, sectors, _stocks = self.compute_all(force_refresh=force_refresh)
        def sort_key(sector: SectorMetrics) -> tuple[bool, float]:
            value = getattr(sector, sort_by)
            if value is None:
                return True, 0.0
            numeric = float(value)
            if not np.isfinite(numeric):
                return True, 0.0
            return False, -numeric if descending else numeric

        return sorted(sectors, key=sort_key)

    def ranked_stocks(
        self,
        *,
        sort_by: str,
        descending: bool = True,
        breakouts_only: bool = False,
        above_ema50_only: bool = False,
        sector: str | None = None,
        limit: int = 50,
        force_refresh: bool = False,
    ) -> list[StockContext]:
        """Filter and rank stock measurements for the market-intelligence view.

        Args:
            sort_by: Stock measurement used for ordering.
            descending: Whether higher values appear first.
            breakouts_only: Keep stocks marked as breakouts.
            above_ema50_only: Keep stocks above their 50-day EMA.
            sector: Keep stocks in this sector, ignoring letter case.
            limit: Maximum number of stock records to return.
            force_refresh: Whether to bypass the cached market snapshot.

        Returns:
            Matching stock measurements in the requested order.

        Raises:
            InvalidMarketIntelSort: If ``sort_by`` is not supported.
        """
        fields = (
            "relative_strength_nifty_20d",
            "relative_volume",
            "from_52w_high_pct",
            "atr_pct",
            "trend_pct",
            "change_1d_pct",
        )
        if sort_by not in fields:
            raise InvalidMarketIntelSort(
                f"sort_by must be one of: {', '.join(sorted(fields))}"
            )
        _summary, _sectors, stocks_by_symbol = self.compute_all(
            force_refresh=force_refresh
        )
        stocks = list(stocks_by_symbol.values())
        if breakouts_only:
            stocks = [stock for stock in stocks if stock.is_breakout]
        if above_ema50_only:
            stocks = [stock for stock in stocks if stock.above_ema50]
        if sector:
            stocks = [
                stock
                for stock in stocks
                if (stock.sector or "").lower() == sector.lower()
            ]
        if sort_by == "from_52w_high_pct":
            stocks.sort(
                key=lambda stock: getattr(stock, sort_by, -999),
                reverse=not descending,
            )
        else:
            stocks.sort(
                key=lambda stock: getattr(stock, sort_by, None) or 0,
                reverse=descending,
            )
        return stocks[:limit]

    def stock_context(
        self, symbol: str, *, force_refresh: bool = False
    ) -> StockContext | None:
        """Return the market context for one canonicalized stock symbol."""
        clean_symbol = canonical_symbol(symbol)
        _summary, _sectors, stocks_by_symbol = self.compute_all(
            force_refresh=force_refresh
        )
        return stocks_by_symbol.get(clean_symbol)

    def set_regime_config(self, config: RegimeModelConfig) -> None:
        with self._lock:
            self.regime_config = config
            # Invalidate cached calculations when model thresholds change
            self._cached_summary = None
            self._cached_sectors = None
            self._cached_stocks = None
            self._cache_time = 0.0

    # --------------------------------------------------------------------------
    # Universe & Sector Mapping
    # --------------------------------------------------------------------------
    def get_sector_map(self) -> dict[str, str]:
        """Return canonical symbol-to-industry names from the market-data port."""
        return dict(self._market_data.sector_map())

    def get_universe_symbols(self) -> list[str]:
        """Return symbols in the configured market-data universe."""
        return self._market_data.universe_symbols()

    # --------------------------------------------------------------------------
    # Frame Loading & Benchmark Provenance
    # --------------------------------------------------------------------------
    def _load_frame(self, symbol: str) -> pd.DataFrame | None:
        """Return one symbol's cached daily bars."""
        return self._market_data.load_frame(symbol)

    def get_benchmark_frame_and_provenance(self) -> tuple[pd.DataFrame | None, BenchmarkProvenance]:
        """Return the preferred benchmark and its provenance record."""
        return self._market_data.benchmark_frame_and_provenance()

    # --------------------------------------------------------------------------
    # Calculation Core
    # --------------------------------------------------------------------------
    def compute_all(
        self, *, force_refresh: bool = False
    ) -> tuple[MarketSummary, list[SectorMetrics], dict[str, StockContext]]:
        """Cached market summary, sector rankings and stock contexts, computed once.

        The screen asks for the summary, the sectors and the stocks at the same
        moment, and each call needs the same full-universe pass. Without a lock
        held across the computation every request ran its own copy in parallel —
        a thundering herd that turned one slow pass into several. Now one caller
        computes and the others wait, then reuse its result.
        """
        waited_from = time.time()
        with self._compute_lock:
            # A caller that queued behind another's computation takes that result
            # instead of repeating it, even when it asked for a refresh: the pass
            # that just finished started after this request did.
            with self._lock:
                filled_while_waiting = (
                    self._cached_summary is not None
                    and self._cached_sectors is not None
                    and self._cached_stocks is not None
                    and self._cache_time >= waited_from
                )
                if filled_while_waiting:
                    return self._cached_summary, self._cached_sectors, self._cached_stocks
            return self._compute_all_uncached(force_refresh=force_refresh)

    def invalidate(self) -> None:
        """Drop the cached pass (new price bars arrived), and the universe-independent frames."""
        with self._lock:
            self._cache_time = 0.0

    def _maybe_refresh_index(self) -> None:
        """Ask the index-refresh port to update stale benchmark history."""
        self._index_refresh.refresh_if_due()

    def _vix_snapshot(self) -> dict[str, Any] | None:
        """India VIX level, its day move and where it sits in its own year. None if uncached."""
        frame = self._market_data.vix_frame()
        if frame is None:
            return None
        frame = frame.sort_values("ts")
        close = frame["close"].astype(float).dropna()
        if len(close) < 20:
            return None
        year = close.tail(self.regime_config.high_low_lookback_bars)
        last = float(close.iloc[-1])
        prev = float(close.iloc[-2])
        return {
            "close": round(last, 2),
            "change_1d_pct": round((last / prev - 1) * 100, 2) if prev else None,
            # Share of the past year's sessions on which VIX closed lower than now.
            "percentile_1y": round(float((year < last).mean() * 100), 0),
            "low_1y": round(float(year.min()), 2),
            "high_1y": round(float(year.max()), 2),
            "as_of": pd.Timestamp(frame["ts"].iloc[-1]).date().isoformat(),
            "series": [round(float(v), 2) for v in close.tail(60)],
        }

    def _compute_all_uncached(
        self, *, force_refresh: bool = False
    ) -> tuple[MarketSummary, list[SectorMetrics], dict[str, StockContext]]:
        self._maybe_refresh_index()
        now = time.time()
        with self._lock:
            if (
                not force_refresh
                and self._cached_summary is not None
                and self._cached_sectors is not None
                and self._cached_stocks is not None
                and (now - self._cache_time) < self._ttl_seconds
            ):
                return self._cached_summary, self._cached_sectors, self._cached_stocks
            cfg = self.regime_config

        # Everything below is a pure function of the cached bars, the universe files
        # and this code, so a stored result with the same fingerprint is the answer.
        fingerprint = self._fingerprint()
        stored = self._snapshot_cache.load(fingerprint)
        if stored is not None:
            with self._lock:
                self._cached_summary, self._cached_sectors, self._cached_stocks = stored
                self._cache_time = time.time()
            return stored

        sector_map = self.get_sector_map()
        universe_symbols = self.get_universe_symbols()
        benchmark_frame, bench_provenance = self.get_benchmark_frame_and_provenance()
        required_benchmark_bars = max(
            cfg.benchmark_month_bars,
            cfg.trend_sma_window,
            cfg.breadth_ema_window,
            cfg.short_ema_window,
            cfg.atr_window + 1,
            cfg.vol_long_window,
        )
        if benchmark_frame is None or len(benchmark_frame) < required_benchmark_bars:
            available = 0 if benchmark_frame is None else len(benchmark_frame)
            raise MarketHistoryUnavailable(
                "Market summary needs a benchmark with at least "
                f"{required_benchmark_bars} bars; got {available}"
            )

        b_closes = benchmark_frame["close"].astype(float)
        nifty_close = float(b_closes.iloc[-1])
        if not np.isfinite(nifty_close) or nifty_close <= 0:
            raise MarketHistoryUnavailable("Benchmark has no valid latest close")
        nifty_1d_pct = float((nifty_close / b_closes.iloc[-2] - 1.0) * 100.0)
        nifty_1m_pct = float(
            (nifty_close / b_closes.iloc[-cfg.benchmark_month_bars] - 1.0) * 100.0
        )
        s_trend = float(calc_sma(b_closes, cfg.trend_sma_window).iloc[-1])
        e20 = float(calc_ema(b_closes, cfg.short_ema_window).iloc[-1])
        e50 = float(calc_ema(b_closes, cfg.breadth_ema_window).iloc[-1])
        if not all(np.isfinite(value) and value > 0 for value in (s_trend, e20, e50)):
            raise MarketHistoryUnavailable("Benchmark history cannot produce its trend averages")
        nifty_trend_pct = (nifty_close / s_trend - 1.0) * 100.0
        nifty_above_ema20 = nifty_close >= e20
        nifty_above_ema50 = nifty_close >= e50

        tr_s = calc_atr(
            benchmark_frame["high"],
            benchmark_frame["low"],
            benchmark_frame["close"],
            cfg.atr_window,
        )
        atr_val = float(tr_s.iloc[-1])
        if not np.isfinite(atr_val) or atr_val < 0:
            raise MarketHistoryUnavailable("Benchmark history cannot produce ATR")
        bench_atr_pct = (atr_val / nifty_close) * 100.0

        highs, lows, closes = (
            benchmark_frame["high"],
            benchmark_frame["low"],
            benchmark_frame["close"],
        )
        tr = np.maximum(
            highs - lows,
            np.maximum(abs(highs - closes.shift()), abs(lows - closes.shift())),
        )
        atr_short = float(tr.rolling(cfg.vol_short_window).mean().iloc[-1])
        atr_long = float(tr.rolling(cfg.vol_long_window).mean().iloc[-1])
        if not np.isfinite(atr_short) or not np.isfinite(atr_long) or atr_long <= 0:
            raise MarketHistoryUnavailable("Benchmark history cannot produce volatility ratio")
        bench_vol_ratio = atr_short / atr_long

        # Scan Universe
        stock_contexts: dict[str, StockContext] = {}
        sector_stocks: dict[str, list[dict[str, Any]]] = {}

        advancing = 0
        declining = 0
        unchanged = 0
        above_ema20_cnt = 0
        above_ema50_cnt = 0
        above_sma200_cnt = 0
        highs_52w_cnt = 0
        lows_52w_cnt = 0
        breakouts: list[dict[str, Any]] = []
        # date -> [stocks above their 50-day average, stocks with a value that day]
        breadth_days: dict[str, list[int]] = {}

        total_scanned = 0
        symbols_to_scan = universe_symbols

        # Parquet reads are I/O bound and release the GIL, so load them together
        # instead of one at a time — reading was ~40% of the pass.
        with ThreadPoolExecutor(max_workers=8) as pool:
            frames = dict(
                zip(
                    symbols_to_scan,
                    pool.map(self._load_frame, symbols_to_scan),
                    strict=True,
                )
            )

        required_stock_bars = max(
            cfg.minimum_stock_history_bars,
            cfg.high_low_lookback_bars,
            cfg.trend_sma_window,
            cfg.breadth_ema_window,
            cfg.breadth_sma_window,
            cfg.relative_strength_bars + 1,
            cfg.sector_return_bars + 1,
            cfg.volume_average_bars + 1,
            cfg.breakout_lookback_bars,
            cfg.short_ema_window,
            cfg.atr_window + 1,
        )
        for sym in symbols_to_scan:
            df = frames[sym]
            if df is None or len(df) < required_stock_bars:
                continue

            closes = df["close"].astype(float)
            highs = df["high"].astype(float)
            lows = df["low"].astype(float)
            volumes = df["volume"].astype(float)
            opens = df["open"].astype(float)

            close = float(closes.iloc[-1])
            prev_close = float(closes.iloc[-2])
            open_price = float(opens.iloc[-1])
            if not all(
                np.isfinite(value) and value > 0
                for value in (close, prev_close, open_price)
            ):
                raise MarketHistoryUnavailable(
                    f"{sym} has an invalid latest OHLC value"
                )
            prior_vol_mean = float(
                volumes.shift(1)
                .rolling(
                    cfg.volume_average_bars,
                    min_periods=cfg.volume_average_min_bars,
                )
                .mean()
                .iloc[-1]
            )
            curr_vol = float(volumes.iloc[-1])
            if not np.isfinite(prior_vol_mean) or prior_vol_mean <= 0 or not np.isfinite(curr_vol):
                raise MarketHistoryUnavailable(
                    f"{sym} has no valid current and prior volume values"
                )
            total_scanned += 1

            chg_1d = (close / prev_close - 1.0) * 100.0
            if chg_1d > cfg.daily_move_threshold_pct:
                advancing += 1
            elif chg_1d < -cfg.daily_move_threshold_pct:
                declining += 1
            else:
                unchanged += 1

            s50 = float(calc_sma(closes, cfg.trend_sma_window).iloc[-1])
            trend_pct = (close / s50 - 1.0) * 100.0

            e20 = float(calc_ema(closes, cfg.short_ema_window).iloc[-1])
            ema50_series = calc_ema(closes, cfg.breadth_ema_window)
            e50 = float(ema50_series.iloc[-1])
            if "ts" in df.columns:
                days = cfg.breadth_history_bars
                flags = closes.tail(days).to_numpy() >= ema50_series.tail(days).to_numpy()
                labels = pd.to_datetime(df["ts"].tail(days)).dt.strftime("%Y-%m-%d").to_numpy()
                for label, flag in zip(labels, flags, strict=True):
                    tally = breadth_days.setdefault(label, [0, 0])
                    tally[0] += int(flag)
                    tally[1] += 1
            s200 = float(calc_sma(closes, cfg.breadth_sma_window).iloc[-1])

            above_20 = close >= e20
            above_50 = close >= e50
            above_200 = close >= s200

            if above_20:
                above_ema20_cnt += 1
            if above_50:
                above_ema50_cnt += 1
            if above_200:
                above_sma200_cnt += 1

            rvol = curr_vol / prior_vol_mean

            lookback_52w = cfg.high_low_lookback_bars
            high_52w = float(highs.rolling(lookback_52w, min_periods=10).max().iloc[-1])
            low_52w = float(lows.rolling(lookback_52w, min_periods=10).min().iloc[-1])

            if high_52w <= 0 or low_52w <= 0:
                raise MarketHistoryUnavailable(f"{sym} has an invalid 52-week range")
            dist_high = (close / high_52w - 1.0) * 100.0
            dist_low = (close / low_52w - 1.0) * 100.0

            if dist_high >= -cfg.near_52w_extreme_pct:
                highs_52w_cnt += 1
            if dist_low <= cfg.near_52w_extreme_pct:
                lows_52w_cnt += 1

            recent_high_20 = float(
                highs.rolling(cfg.breakout_lookback_bars).max().iloc[-1]
            )
            is_breakout = (
                close >= recent_high_20 * cfg.breakout_proximity_pct
                and rvol >= cfg.breakout_relative_volume
                and chg_1d > 0
            )

            tr_series = calc_atr(highs, lows, closes, cfg.atr_window)
            atr_val = float(tr_series.iloc[-1])
            if not np.isfinite(atr_val) or atr_val < 0:
                raise MarketHistoryUnavailable(f"{sym} has no valid ATR value")
            atr_pct = atr_val / close * 100.0

            gap_pct = (open_price / prev_close - 1.0) * 100.0

            ret_20d = float(
                (close / closes.iloc[-(cfg.relative_strength_bars + 1)] - 1.0)
                * 100.0
            )
            rs_nifty = ret_20d - nifty_1m_pct

            sec = sector_map.get(sym)

            stock_item = {
                "symbol": sym,
                "close": round(close, 2),
                "change_1d_pct": round(chg_1d, 2),
                "return_20d_pct": round(ret_20d, 2),
                "return_5d_pct": round(
                    (
                        close
                        / float(closes.iloc[-(cfg.sector_return_bars + 1)])
                        - 1.0
                    )
                    * 100.0,
                    2,
                ),
                "trend_pct": round(trend_pct, 2),
                "rs_nifty_20d": round(rs_nifty, 2),
                "relative_volume": round(rvol, 2),
                "atr_pct": round(atr_pct, 2),
                "from_52w_high_pct": round(dist_high, 2),
                "from_52w_low_pct": round(dist_low, 2),
                "gap_pct": round(gap_pct, 2),
                "above_ema20": above_20,
                "above_ema50": above_50,
                "is_breakout": is_breakout,
                "sector": sec,
                "volume": curr_vol,
                "prior_vol_mean": prior_vol_mean,
            }

            if sec is not None:
                sector_stocks.setdefault(sec, []).append(stock_item)

            if is_breakout:
                breakouts.append(stock_item)

            stock_contexts[sym] = StockContext(
                symbol=sym,
                close=round(close, 2),
                change_1d_pct=round(chg_1d, 2),
                trend_pct=round(trend_pct, 2),
                relative_strength_nifty_20d=round(rs_nifty, 2),
                relative_volume=round(rvol, 2),
                atr_pct=round(atr_pct, 2),
                from_52w_high_pct=round(dist_high, 2),
                from_52w_low_pct=round(dist_low, 2),
                gap_pct=round(gap_pct, 2),
                above_ema20=above_20,
                above_ema50=above_50,
                is_breakout=is_breakout,
                sector=sec,
                sector_relative_strength_1m=None,
                market_breadth_pct=None,
                benchmark_provenance=bench_provenance,
                regime_model_version=cfg.version,
                as_of=datetime.now(UTC).isoformat(),
            )

        if total_scanned == 0:
            raise MarketHistoryUnavailable(
                "Market summary has no symbols with enough valid history"
            )
        n_scanned = total_scanned
        breadth_20 = round((above_ema20_cnt / n_scanned) * 100.0, 1)
        breadth_50 = round((above_ema50_cnt / n_scanned) * 100.0, 1)
        breadth_200 = round((above_sma200_cnt / n_scanned) * 100.0, 1)
        ad_ratio = round(advancing / declining, 2) if declining else None

        sector_metrics_list, positive_sectors = aggregate_sectors(
            sector_stocks,
            nifty_1d_pct=nifty_1d_pct,
            nifty_1m_pct=nifty_1m_pct,
            config=cfg,
        )

        sector_rs_lookup = {s.sector: s.relative_strength_1m for s in sector_metrics_list}
        for ctx in stock_contexts.values():
            if ctx.sector:
                ctx.sector_relative_strength_1m = sector_rs_lookup.get(ctx.sector)
            ctx.market_breadth_pct = breadth_50

        if not sector_metrics_list:
            raise MarketHistoryUnavailable(
                "Market summary has no sector mappings for the available symbols"
            )
        tot_sectors = len(sector_metrics_list)
        sector_part_pct = round((positive_sectors / tot_sectors) * 100.0, 1)

        # 1. Raw measurements store
        raw_measurements = RawMarketMeasurements(
            nifty_trend_pct=round(nifty_trend_pct, 4),
            breadth_above_ema50_pct=breadth_50,
            breadth_above_ema20_pct=breadth_20,
            breadth_above_sma200_pct=breadth_200,
            advancing_stocks=advancing,
            declining_stocks=declining,
            advance_decline_ratio=ad_ratio,
            atr_pct=round(bench_atr_pct, 4),
            volatility_ratio=round(bench_vol_ratio, 4),
            sector_participation_pct=sector_part_pct,
            benchmark_provenance=bench_provenance,
            regime_model_version=cfg.version,
            as_of=datetime.now(UTC).isoformat(),
        )

        # 2. Derive categorical regime classification from raw measurements
        regime_info = classify_regime_from_raw(raw_measurements, config=cfg)

        nifty_score = min(max((nifty_trend_pct + 5.0) * 10.0, 0.0), 100.0)
        trend_strength = round(0.4 * nifty_score + 0.4 * breadth_50 + 0.2 * sector_part_pct, 1)

        breakouts.sort(key=lambda x: x["relative_volume"], reverse=True)
        top_rs_stocks = sorted(
            [asdict(s) for s in stock_contexts.values()],
            key=lambda x: x["relative_strength_nifty_20d"],
            reverse=True,
        )

        strongest_sectors = [s.sector for s in sector_metrics_list[:3]]
        weakest_sectors = (
            [s.sector for s in sector_metrics_list[-3:]] if len(sector_metrics_list) >= 3 else []
        )

        benchmark_series: list[dict[str, Any]] = []
        if len(benchmark_frame) >= 20:
            bclose = benchmark_frame["close"].astype(float)
            b50 = calc_sma(bclose, 50)
            b200 = calc_sma(bclose, 200)
            bdates = (
                pd.to_datetime(benchmark_frame["ts"])
                if "ts" in benchmark_frame.columns
                else pd.to_datetime(benchmark_frame.index)
            )
            for i in range(
                max(0, len(bclose) - cfg.benchmark_chart_bars), len(bclose)
            ):
                benchmark_series.append(
                    {
                        "d": bdates.iloc[i].strftime("%Y-%m-%d"),
                        "c": round(float(bclose.iloc[i]), 2),
                        "s50": round(float(b50.iloc[i]), 2) if pd.notna(b50.iloc[i]) else None,
                        "s200": round(float(b200.iloc[i]), 2) if pd.notna(b200.iloc[i]) else None,
                    }
                )

        nifty_52w_high = nifty_52w_low = None
        if len(benchmark_frame) >= cfg.high_low_lookback_bars:
            year = benchmark_frame.tail(cfg.high_low_lookback_bars)
            nifty_52w_high = round(float(year["high"].astype(float).max()), 2)
            nifty_52w_low = round(float(year["low"].astype(float).min()), 2)

        vix = self._vix_snapshot()

        # A sector whose week disagrees with its month is changing direction.
        # Tiny sectors (a handful of stocks) swing too much to be worth reporting.
        sized = [
            s
            for s in sector_metrics_list
            if s.stock_count >= cfg.minimum_sector_stock_count
        ]
        sectors_turning_up = [
            s.sector
            for s in sorted(sized, key=lambda s: s.return_1w_pct, reverse=True)
            if s.return_1w_pct > 0 and s.return_1m_pct < 0
        ][:3]
        sectors_fading = [
            s.sector
            for s in sorted(sized, key=lambda s: s.return_1w_pct)
            if s.return_1w_pct < 0 and s.return_1m_pct > 0
        ][:3]

        # Only days most stocks reported for: a partial day would swing the line.
        widest = max((t[1] for t in breadth_days.values()), default=0)
        breadth_history = [
            {"d": d, "pct": round(100.0 * t[0] / t[1], 1)}
            for d, t in sorted(breadth_days.items())
            if widest and t[1] >= cfg.breadth_history_min_coverage * widest
        ]

        summary = MarketSummary(
            as_of=datetime.now(UTC).isoformat(),
            nifty_close=round(nifty_close, 2),
            nifty_change_1d_pct=round(nifty_1d_pct, 2),
            nifty_trend_pct=round(nifty_trend_pct, 2),
            nifty_above_ema20=nifty_above_ema20,
            nifty_above_ema50=nifty_above_ema50,
            nifty_1m_return_pct=round(nifty_1m_pct, 2),
            benchmark_provenance=bench_provenance,
            regime=regime_info,
            raw_measurements=raw_measurements,
            total_stocks_analyzed=total_scanned,
            symbols_without_required_history=len(universe_symbols) - total_scanned,
            advancing_stocks=advancing,
            declining_stocks=declining,
            unchanged_stocks=unchanged,
            advance_decline_ratio=ad_ratio,
            breadth_above_ema20_pct=breadth_20,
            breadth_above_ema50_pct=breadth_50,
            breadth_above_sma200_pct=breadth_200,
            highs_52w_count=highs_52w_cnt,
            lows_52w_count=lows_52w_cnt,
            market_volatility_atr_pct=round(bench_atr_pct, 2),
            volatility_ratio=round(bench_vol_ratio, 2),
            market_trend_strength=trend_strength,
            sector_participation_pct=sector_part_pct,
            strongest_sectors=strongest_sectors,
            weakest_sectors=weakest_sectors,
            top_breakouts=breakouts[:15],
            top_relative_strength_stocks=top_rs_stocks[:15],
            benchmark_series=benchmark_series,
            breadth_history=breadth_history,
            nifty_52w_high=nifty_52w_high,
            nifty_52w_low=nifty_52w_low,
            sectors_turning_up=sectors_turning_up,
            sectors_fading=sectors_fading,
            vix=vix,
            stocks_as_of=(breadth_history[-1]["d"] if breadth_history else None),
        )

        with self._lock:
            self._cached_summary = summary
            self._cached_sectors = sector_metrics_list
            self._cached_stocks = stock_contexts
            self._cache_time = time.time()

        self._snapshot_cache.save(
            fingerprint, (summary, sector_metrics_list, stock_contexts)
        )
        return summary, sector_metrics_list, stock_contexts

    # --------------------------------------------------------------------------
    # Stored result: a restart should not redo a pass whose inputs have not changed
    # --------------------------------------------------------------------------
    def _fingerprint(self) -> str:
        from forward.market_intel import models

        return self._market_data.fingerprint(
            self.regime_config,
            (
                Path(__file__),
                Path(models.__file__),
                Path(__file__).with_name("regime.py"),
                Path(__file__).with_name("sectors.py"),
            ),
        )

    # --------------------------------------------------------------------------
    # Public Accessors
    # --------------------------------------------------------------------------
    def get_summary(self, *, force_refresh: bool = False) -> MarketSummary:
        summary, _, _ = self.compute_all(force_refresh=force_refresh)
        return summary

    def get_sectors(
        self,
        *,
        sort_by: str = "relative_strength_1m",
        ascending: bool = False,
        force_refresh: bool = False,
    ) -> list[SectorMetrics]:
        return self.ranked_sectors(
            sort_by=sort_by,
            descending=not ascending,
            force_refresh=force_refresh,
        )

    def get_stock_context(self, symbol: str) -> StockContext | None:
        clean = canonical_symbol(symbol)
        summary, sectors, stocks = self.compute_all()
        if clean in stocks:
            return stocks[clean]

        df = self._market_data.load_frame(clean)
        if df is None:
            return None

        cfg = self.get_regime_config()
        required_stock_bars = max(
            cfg.minimum_stock_history_bars,
            cfg.high_low_lookback_bars,
            cfg.trend_sma_window,
            cfg.breadth_ema_window,
            cfg.breadth_sma_window,
            cfg.relative_strength_bars + 1,
            cfg.volume_average_bars + 1,
            cfg.breakout_lookback_bars,
            cfg.short_ema_window,
            cfg.atr_window + 1,
        )
        if len(df) < required_stock_bars:
            raise MarketHistoryUnavailable(
                f"{clean} needs {required_stock_bars} bars for its market context"
            )
        sector_map = self.get_sector_map()
        sec = sector_map.get(clean)
        closes = df["close"].astype(float)
        highs = df["high"].astype(float)
        lows = df["low"].astype(float)
        volumes = df["volume"].astype(float)
        opens = df["open"].astype(float)

        close = float(closes.iloc[-1])
        prev_close = float(closes.iloc[-2])
        if close <= 0 or prev_close <= 0 or not np.isfinite(close + prev_close):
            raise MarketHistoryUnavailable(f"{clean} has an invalid latest close")
        chg_1d = (close / prev_close - 1.0) * 100.0

        s50 = float(calc_sma(closes, cfg.trend_sma_window).iloc[-1])
        if not np.isfinite(s50) or s50 <= 0:
            raise MarketHistoryUnavailable(f"{clean} has no valid trend average")
        trend_pct = (close / s50 - 1.0) * 100.0
        e20 = float(calc_ema(closes, cfg.short_ema_window).iloc[-1])
        e50 = float(calc_ema(closes, cfg.breadth_ema_window).iloc[-1])
        if not np.isfinite(e20) or not np.isfinite(e50):
            raise MarketHistoryUnavailable(f"{clean} has no valid trend averages")

        prior_vol_mean = float(
            volumes.shift(1)
            .rolling(
                cfg.volume_average_bars,
                min_periods=cfg.volume_average_min_bars,
            )
            .mean()
            .iloc[-1]
        )
        if not np.isfinite(prior_vol_mean) or prior_vol_mean <= 0:
            raise MarketHistoryUnavailable(f"{clean} has no valid prior volume average")
        rvol = float(volumes.iloc[-1]) / prior_vol_mean

        lookback_52w = cfg.high_low_lookback_bars
        high_52w = float(highs.rolling(lookback_52w, min_periods=10).max().iloc[-1])
        low_52w = float(lows.rolling(lookback_52w, min_periods=10).min().iloc[-1])
        if high_52w <= 0 or low_52w <= 0:
            raise MarketHistoryUnavailable(f"{clean} has an invalid 52-week range")
        dist_high = (close / high_52w - 1.0) * 100.0
        dist_low = (close / low_52w - 1.0) * 100.0

        tr_series = calc_atr(highs, lows, closes, cfg.atr_window)
        atr_val = float(tr_series.iloc[-1])
        if not np.isfinite(atr_val) or atr_val < 0:
            raise MarketHistoryUnavailable(f"{clean} has no valid ATR value")
        atr_pct = atr_val / close * 100.0

        gap_pct = (float(opens.iloc[-1]) / prev_close - 1.0) * 100.0
        recent_high_20 = float(
            highs.rolling(cfg.breakout_lookback_bars).max().iloc[-1]
        )
        is_breakout = (
            close >= recent_high_20 * cfg.breakout_proximity_pct
            and rvol >= cfg.breakout_relative_volume
        )

        ret_20d = float(
            (close / closes.iloc[-(cfg.relative_strength_bars + 1)] - 1.0)
            * 100.0
        )
        rs_nifty = ret_20d - summary.nifty_1m_return_pct

        sec_rs_val = next(
            (s.relative_strength_1m for s in sectors if s.sector == sec), None
        ) if sec is not None else None

        return StockContext(
            symbol=clean,
            close=round(close, 2),
            change_1d_pct=round(chg_1d, 2),
            trend_pct=round(trend_pct, 2),
            relative_strength_nifty_20d=round(rs_nifty, 2),
            relative_volume=round(rvol, 2),
            atr_pct=round(atr_pct, 2),
            from_52w_high_pct=round(dist_high, 2),
            from_52w_low_pct=round(dist_low, 2),
            gap_pct=round(gap_pct, 2),
            above_ema20=close >= e20,
            above_ema50=close >= e50,
            is_breakout=is_breakout,
            sector=sec,
            sector_relative_strength_1m=sec_rs_val,
            market_breadth_pct=summary.breadth_above_ema50_pct,
            benchmark_provenance=summary.benchmark_provenance,
            regime_model_version=cfg.version,
            as_of=datetime.now(UTC).isoformat(),
        )
