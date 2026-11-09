"""SQL adapter for paper account executions."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from forward.appdb.deployment_repository import DeploymentRepository
from forward.appdb.engine import AppDatabase
from forward.appdb.order_event_repository import OrderEventRepository
from forward.appdb.order_repository import OrderRepository
from forward.core.enums import AssetClass, Side
from forward.core.instrument_models import Instrument
from forward.core.order_models import Fill
from forward.core.paper_ledger_ports import PaperLedgerStoragePort


class SqlPaperLedgerStorage(PaperLedgerStoragePort):
    """Read paper fills, deployment capital, and activity from SQL."""

    def __init__(self, database: AppDatabase) -> None:
        """Create the adapter.

        Args:
            database: The application database connection manager.
        """
        self._database = database

    def deployment_cash(self, user_id: str, deployment_id: str) -> float:
        """Return capital assigned to an account-owned deployment.

        Args:
            user_id: Account that owns the deployment.
            deployment_id: Deployment whose capital is requested.

        Returns:
            The saved capital allocation.

        Raises:
            LookupError: If the deployment does not belong to the account.
        """
        with self._database.session() as session:
            deployment = DeploymentRepository.get(session, deployment_id, user_id)
        if deployment is None:
            raise LookupError(
                f"deployment {deployment_id!r} does not belong to account {user_id!r}"
            )
        return float(deployment["capital"])

    def fills(self, user_id: str, *, deployment_id: str | None, mode: str | None) -> list[Fill]:
        """Read all incremental fills in execution-time order.

        Args:
            user_id: Account whose executions are requested.
            deployment_id: Optional deployment filter.
            mode: Optional execution-mode filter.

        Returns:
            The matching fills, oldest first.
        """
        orders_by_id = self._orders_by_id(user_id, deployment_id=deployment_id, mode=mode)
        return sorted(self._fills_by_order(orders_by_id), key=lambda fill: fill.ts)

    def fills_by_deployment(
        self, user_id: str, deployment_ids: Sequence[str]
    ) -> dict[str, list[Fill]]:
        """Read fills for several deployments in one storage pass.

        Args:
            user_id: Account whose executions are requested.
            deployment_ids: Deployments whose fills are requested.

        Returns:
            Fills grouped by deployment identifier.
        """
        with self._database.session() as session:
            orders = OrderRepository.for_replay(session, user_id)
        deployment_set = set(deployment_ids)
        orders_by_id = {
            order["order_id"]: order
            for order in orders
            if order.get("deployment_id") in deployment_set
        }
        fills_by_order = self._fills_by_order(orders_by_id)
        result = {deployment_id: [] for deployment_id in deployment_ids}
        for fill in fills_by_order:
            deployment_id = orders_by_id[fill.order_id]["deployment_id"]
            result[deployment_id].append(fill)
        for fills in result.values():
            fills.sort(key=lambda fill: fill.ts)
        return result

    def _orders_by_id(
        self, user_id: str, *, deployment_id: str | None, mode: str | None
    ) -> dict[str, dict[str, Any]]:
        with self._database.session() as session:
            rows = OrderRepository.for_replay(
                session, user_id, deployment_id=deployment_id, mode=mode
            )
        return {row["order_id"]: row for row in rows}

    def _fills_by_order(self, orders: dict[str, dict[str, Any]]) -> list[Fill]:
        with self._database.session() as session:
            events_by_order = OrderEventRepository.fills_for_orders(
                session, list(orders), include_state_changes=True
            )

        fills: list[Fill] = []
        for order_id, order in orders.items():
            previous_quantity = 0.0
            for event in events_by_order.get(order_id, []):
                if event.get("filled_qty") is None:
                    continue
                cumulative_quantity = float(event["filled_qty"])
                quantity = cumulative_quantity - previous_quantity
                previous_quantity = cumulative_quantity
                if event["to_status"] not in ("PARTIALLY_FILLED", "FILLED"):
                    continue
                if quantity <= 0:
                    continue
                fills.append(_fill_from_event(order, event, quantity))

        return fills

    def activity(self, user_id: str, *, limit: int) -> dict[str, Any]:
        """Read recent paper orders and their total count.

        Args:
            user_id: Account whose paper activity is requested.
            limit: Maximum number of orders to return.

        Returns:
            The selected order fields and total matching order count.
        """
        with self._database.session() as session:
            rows, total = OrderRepository.list_for_user(session, user_id, mode="PAPER", limit=limit)
        fields = (
            "order_id",
            "deployment_id",
            "strategy_id",
            "symbol",
            "side",
            "quantity",
            "status",
            "avg_fill_price",
            "filled_quantity",
            "requested_price",
            "reject_reason",
            "created_at",
            "completed_at",
        )
        return {"orders": [{key: row.get(key) for key in fields} for row in rows], "total": total}


def _fill_from_event(order: dict[str, Any], event: dict[str, Any], quantity: float) -> Fill:
    """Build a domain fill from one saved execution event.

    Args:
        order: The stored order that produced the event.
        event: The execution event with its actual fill price and fee.
        quantity: The incremental fill quantity represented by the event.

    Returns:
        The domain fill used by portfolio replay.
    """
    side = Side[order["side"].upper()]
    instrument = Instrument(
        symbol=order["symbol"],
        exchange=order["exchange"],
        asset_class=AssetClass(order["asset_class"]),
    )
    price = float(event["filled_price"])
    raw = event.get("raw")
    if isinstance(raw, str):
        raw = json.loads(raw)
    if (
        order.get("mode") == "LIVE"
        and isinstance(raw, dict)
        and raw.get("incremental_fill_price") is not None
    ):
        price = float(raw["incremental_fill_price"])

    reference = order.get("requested_price")
    slippage = 0.0
    if reference and price:
        slippage_fraction = (price - float(reference)) / float(reference)
        if side is Side.SELL:
            slippage_fraction = -slippage_fraction
        slippage = slippage_fraction * float(reference)

    return Fill(
        order_id=order["order_id"],
        instrument=instrument,
        side=side,
        quantity=quantity,
        price=price,
        ts=event["fill_ts"],
        commission=(
            None if event.get("commission") is None else float(event["commission"])
        ),
        slippage=slippage,
    )
