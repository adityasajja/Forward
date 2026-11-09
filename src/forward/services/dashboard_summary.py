"""Dashboard P&L, performance, and market breadth calculations."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from forward.analytics.dashboard_metrics import (
    BREADTH_MINIMUM_BARS,
    BREADTH_SAMPLE_SIZE,
    BREADTH_SESSIONS,
    calculate_breadth,
    performance_from_trades,
)
from forward.config.settings import get_settings
from forward.core.dashboard_history_ports import DashboardHistoryPort
from forward.core.serialization import json_safe
from forward.core.session import IST
from forward.services.broker_account_read import BrokerAccountReadService

_BREADTH_LOOKBACK_BARS = BREADTH_MINIMUM_BARS + BREADTH_SESSIONS - 1


class DashboardSummaryCache:
    """Hold the short-lived dashboard results shared by API requests."""

    def __init__(self) -> None:
        """Create an empty dashboard cache."""
        self.summary: dict[str, Any] | None = None
        self.summary_at = 0.0
        self.summary_exchange = ""
        self.breadth: dict[str, Any] | None = None
        self.breadth_at = 0.0
        self.breadth_exchange = ""


class DashboardSummaryService:
    """Build the dashboard summary from broker reads and cached daily history."""

    def __init__(
        self,
        history: DashboardHistoryPort,
        cache: DashboardSummaryCache,
    ) -> None:
        """Create the dashboard summary service.

        Args:
            history: Adapter that reads daily closes from the local cache.
            cache: Shared short-lived result cache.
        """
        self._history = history
        self._cache = cache
        self._settings = get_settings()

    def summary(
        self,
        account_provider: Callable[[], BrokerAccountReadService],
        exchange: str | None = None,
    ) -> dict[str, Any]:
        """Build the dashboard KPI data for one exchange.

        Args:
            account_provider: Creates the broker read service for this request.
            exchange: Exchange used for positions and breadth history.

        Returns:
            Position P&L, trade performance, and market breadth.
        """
        normalized_exchange = (exchange or self._settings.chart_exchange).upper()
        now = time.monotonic()
        if (
            self._cache.summary is not None
            and self._cache.summary_exchange == normalized_exchange
            and now - self._cache.summary_at < self._settings.dashboard_summary_ttl_seconds
        ):
            return self._cache.summary

        account = account_provider()
        result: dict[str, Any] = {
            "as_of": datetime.now(UTC).isoformat(timespec="seconds"),
            "exchange": normalized_exchange,
        }
        with ThreadPoolExecutor(max_workers=self._settings.dashboard_broker_read_workers) as pool:
            positions_future = pool.submit(account.read, "positions")
            trades_future = pool.submit(account.read, "trades")

            try:
                broker_positions = positions_future.result()
                positions = (
                    []
                    if broker_positions.error
                    else json_safe(broker_positions.rows)
                )
                result["positions"] = self._position_summary(positions)
            except Exception as exc:  # noqa: BLE001 - each broker read reports its own error
                result["positions"] = {"count": 0, "error": str(exc)[:200]}

            try:
                broker_trades = trades_future.result()
                trades = (
                    []
                    if broker_trades.error
                    else json_safe(broker_trades.rows)
                )
                result["performance"] = performance_from_trades(
                    trades, today=datetime.now(UTC).astimezone(IST).date()
                )
            except Exception as exc:  # noqa: BLE001 - each broker read reports its own error
                result["performance"] = {"error": str(exc)[:200], "trades": 0}

        day_pnl = result["positions"].get("day_pnl")
        realized_today = result["performance"].get("realized_pnl_today")
        if not result["positions"].get("day_pnl_complete", False):
            result["positions"]["total_day_pnl"] = None
        elif day_pnl is not None or realized_today is not None:
            result["positions"]["total_day_pnl"] = round(
                (day_pnl or 0.0) + (realized_today or 0.0), 2
            )

        try:
            result["breadth"] = self.breadth_trend(normalized_exchange)
        except Exception as exc:  # noqa: BLE001 - breadth errors do not hide account metrics
            result["breadth"] = {"error": str(exc)[:200], "series": []}

        self._cache.summary = result
        self._cache.summary_at = now
        self._cache.summary_exchange = normalized_exchange
        return result

    def breadth_trend(self, exchange: str | None = None) -> dict[str, Any]:
        """Calculate the share of sampled symbols above their moving averages.

        Args:
            exchange: Exchange whose cached history is used.

        Returns:
            Five-session breadth, sampled symbol count, and universe size.
        """
        normalized_exchange = (exchange or self._settings.chart_exchange).upper()
        now = time.monotonic()
        if (
            self._cache.breadth is not None
            and self._cache.breadth_exchange == normalized_exchange
            and now - self._cache.breadth_at < self._settings.dashboard_breadth_ttl_seconds
        ):
            return self._cache.breadth

        history = self._history.breadth_history(
            normalized_exchange,
            BREADTH_SAMPLE_SIZE,
            _BREADTH_LOOKBACK_BARS,
            BREADTH_MINIMUM_BARS,
        )
        result = calculate_breadth(history)
        self._cache.breadth = result
        self._cache.breadth_at = now
        self._cache.breadth_exchange = normalized_exchange
        return result

    def _position_summary(
        self, positions: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Calculate open value and daily P&L for broker positions."""
        total = day = invested = 0.0
        counted = no_prev = 0
        for position in positions:
            quantity = float(
                self._pick(
                    position,
                    "netQuantity",
                    "NetQuantity",
                    "quantity",
                    "qty",
                    "Quantity",
                    default=0,
                )
                or 0
            )
            if quantity == 0:
                continue
            last = float(
                self._pick(
                    position,
                    "ltp",
                    "LTP",
                    "lastTradedPrice",
                    "lastPrice",
                    "last_price",
                    default=0,
                )
                or 0
            )
            average = float(
                self._pick(
                    position,
                    "averagePrice",
                    "AveragePrice",
                    "avgPrice",
                    "avg_price",
                    default=0,
                )
                or 0
            )
            previous_close = self._pick(
                position,
                "close",
                "prev_close",
                "previous_close",
                "previousClose",
                "prevClose",
                default=None,
            )
            invested += quantity * average
            total += quantity * last
            counted += 1
            try:
                previous_value = float(previous_close)
            except (TypeError, ValueError):
                previous_value = None
            if previous_value is not None and (
                not math.isfinite(previous_value) or previous_value <= 0
            ):
                previous_value = None

            if previous_value is not None:
                day += quantity * (last - previous_value)
            else:
                no_prev += 1

        return {
            "count": counted,
            "value": round(total, 2),
            "invested": round(invested, 2),
            "day_pnl": round(day, 2) if no_prev == 0 else None,
            "day_pnl_complete": counted == 0 or no_prev == 0,
            "day_pnl_incomplete": no_prev,
            "day_pnl_pct": (
                round((day / invested * 100.0) if invested else 0.0, 3)
                if no_prev == 0
                else None
            ),
            "unrealized_pnl": round(total - invested, 2),
            "last_flat_at": self._last_flat_at(positions),
        }

    @staticmethod
    def _pick(
        row: dict[str, Any], *keys: str, default: Any = None
    ) -> Any:
        """Return the first present, non-empty value among the given keys."""
        for key in keys:
            if key in row and row[key] not in (None, ""):
                return row[key]
        return default

    @staticmethod
    def _last_flat_at(positions: list[dict[str, Any]]) -> str | None:
        """Return the latest flat-book timestamp reported by the broker."""
        for position in positions:
            for key in ("last_flat_at", "flat_at", "as_of", "updated_at"):
                if position.get(key):
                    return str(position[key])
        return None
