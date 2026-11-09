"""Manage the user's saved custom scan definitions."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from forward.core.research_ports import (
    SavedScanStoragePort,
)

DEFAULT_SCANS: list[dict[str, Any]] = [
    {
        "id": "oversold_uptrend",
        "name": "Oversold in Uptrend",
        "combine": "AND",
        "conditions": [
            {"indicator": "rsi", "period": 14, "op": "<", "rhs_type": "value", "rhs_value": 35},
            {
                "indicator": "close",
                "op": ">",
                "rhs_type": "indicator",
                "rhs_indicator": "sma",
                "rhs_period": 50,
            },
        ],
    },
    {
        "id": "fresh_breakout",
        "name": "Fresh Breakout",
        "combine": "AND",
        "conditions": [
            {"indicator": "vs_high", "op": ">", "rhs_type": "value", "rhs_value": -3},
            {"indicator": "vol_x", "op": ">", "rhs_type": "value", "rhs_value": 2.0},
        ],
    },
    {
        "id": "golden_cross",
        "name": "Golden Cross (recent)",
        "combine": "AND",
        "conditions": [
            {
                "indicator": "sma",
                "period": 20,
                "op": "crosses_above",
                "rhs_type": "indicator",
                "rhs_indicator": "sma",
                "rhs_period": 50,
            },
        ],
    },
    {
        "id": "rsi_momentum",
        "name": "RSI Momentum Zone",
        "combine": "AND",
        "conditions": [
            {"indicator": "rsi", "period": 14, "op": ">=", "rhs_type": "value", "rhs_value": 55},
            {"indicator": "rsi", "period": 14, "op": "<=", "rhs_type": "value", "rhs_value": 70},
            {
                "indicator": "close",
                "op": ">",
                "rhs_type": "indicator",
                "rhs_indicator": "sma",
                "rhs_period": 20,
            },
        ],
    },
    {
        "id": "near_52w_high",
        "name": "Near 52-week High",
        "combine": "AND",
        "conditions": [
            {"indicator": "vs_high", "op": ">", "rhs_type": "value", "rhs_value": -5},
            {"indicator": "vol_x", "op": ">", "rhs_type": "value", "rhs_value": 1.5},
        ],
    },
]


class SavedScanService:
    """Load, replace, and delete saved custom scans."""

    def __init__(
        self,
        storage: SavedScanStoragePort,
        defaults: list[dict[str, Any]],
    ) -> None:
        """Create the saved scan service.

        Args:
            storage: Port that stores user scan definitions.
            defaults: Built-in scans used when storage has no readable file.
        """
        self._storage = storage
        self._defaults = defaults

    def list_scans(self) -> list[dict[str, Any]]:
        """Return saved scans, seeding built-ins only for a new store.

        Returns:
            Saved scans in their stored order.
        """
        if self._storage.exists():
            return self._load_scans()

        scans = deepcopy(self._defaults)
        self._storage.save_all(scans)
        return scans

    def upsert(self, scan_id: str, scan: dict[str, Any]) -> dict[str, Any]:
        """Create or replace one saved scan.

        Args:
            scan_id: ID assigned by the route.
            scan: Scan definition supplied by the operator.

        Returns:
            The saved scan definition.
        """
        entry = {**scan, "id": scan_id}
        scans = [saved for saved in self._load_scans() if saved["id"] != scan_id]
        scans.append(entry)
        self._storage.save_all(scans)
        return entry

    def delete(self, scan_id: str) -> None:
        """Delete one saved scan by ID.

        Args:
            scan_id: ID of the scan to delete.
        """
        scans = [saved for saved in self._load_scans() if saved["id"] != scan_id]
        self._storage.save_all(scans)

    def _load_scans(self) -> list[dict[str, Any]]:
        """Read saved scans and surface storage errors to the caller.

        Returns:
            Saved scans in their stored order.
        """
        return self._storage.load_all()
