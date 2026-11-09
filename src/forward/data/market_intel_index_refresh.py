"""Refresh the local index series used by market intelligence."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal

from loguru import logger

from forward.core.market_intel_ports import (
    MarketIntelIndexRefreshPort,
)


class LocalMarketIntelIndexRefreshAdapter(MarketIntelIndexRefreshPort):
    """Refresh NIFTY 50 and India VIX with the configured broker client.

    Args:
        data_root: Configured root that stores index history.
        broker_client: Function that returns an authenticated broker client.
    """

    def __init__(
        self,
        data_root: Path,
        broker_client: Callable[[], Any],
        *,
        base_url: str,
        contract_source: Literal["CACHE", "HTTP"],
        intervals: Mapping[str, Literal["1d", "15m"]],
    ) -> None:
        self._data_root = data_root
        self._broker_client = broker_client
        self._base_url = base_url
        self._contract_source = contract_source
        self._intervals = dict(intervals)
        self._last_attempt = 0.0

    def refresh_if_due(self) -> None:
        """Refresh index files once a day, with at most one attempt per hour."""
        from forward.data.indices import (
            INDIA_VIX,
            NIFTY_50,
            sync_index,
            synced_today,
        )

        names = [
            name
            for name in (NIFTY_50, INDIA_VIX)
            if not synced_today(self._data_root, name)
        ]
        missing_intervals = [name for name in names if name not in self._intervals]
        if missing_intervals:
            raise ValueError(f"index intervals are not configured for: {missing_intervals}")
        now = time.time()
        if not names or now - self._last_attempt < 3600:
            return
        self._last_attempt = now

        try:
            client = self._broker_client()
        except Exception as exc:
            logger.warning("Could not refresh index history: {}", exc)
            return

        def refresh() -> None:
            for name in names:
                result = sync_index(
                    client,
                    self._data_root,
                    name=name,
                    base_url=self._base_url,
                    contract_source=self._contract_source,
                    interval=self._intervals[name],
                )
                if result != "ok":
                    logger.warning("Index history refresh failed for {}: {}", name, result)

        threading.Thread(
            target=refresh, name="index-sync", daemon=True
        ).start()
