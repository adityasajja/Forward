"""SQL adapter for trade-attribution records."""

from __future__ import annotations

from typing import Any

from forward.appdb.deployment_repository import DeploymentRepository
from forward.appdb.engine import AppDatabase
from forward.appdb.order_event_repository import OrderEventRepository
from forward.appdb.order_repository import OrderRepository
from forward.appdb.signal_context_repository import SignalContextRepository
from forward.appdb.trade_attribution_repository import TradeAttributionRepository
from forward.appdb.trade_journal_repository import TradeJournalRepository
from forward.core.attribution_ports import (
    AttributionStoragePort,
)


class SqlAttributionStorage(AttributionStoragePort):
    """Store attribution data in the application database."""

    def __init__(self, database: AppDatabase) -> None:
        """Create the adapter for one application database.

        Args:
            database: Database that owns orders, journals, and attributions.
        """
        self._database = database

    def episodes(
        self,
        user_id: str,
        *,
        deployment_id: str | None,
        closed_only: bool,
        limit: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """Read journal episodes and their total count.

        Args:
            user_id: Account that owns the episodes.
            deployment_id: Deployment to filter by, or ``None`` for the account.
            closed_only: Whether to include only closed episodes.
            limit: Maximum number of episodes to return.

        Returns:
            The selected episodes and the total matching episode count.
        """
        with self._database.session() as session:
            return TradeJournalRepository.list_for_user(
                session,
                user_id,
                deployment_id=deployment_id,
                closed_only=closed_only,
                limit=limit,
            )

    def fingerprint(self, trade_id: str) -> str | None:
        """Return the saved input fingerprint for a trade.

        Args:
            trade_id: Trade whose attribution fingerprint is requested.

        Returns:
            The saved fingerprint, or ``None`` when no attribution exists.
        """
        with self._database.session() as session:
            return TradeAttributionRepository.fingerprint_for(session, trade_id)

    def save(self, row: dict[str, Any]) -> str:
        """Insert or replace a trade attribution.

        Args:
            row: Complete attribution row prepared by the application service.

        Returns:
            ``"inserted"`` or ``"replaced"``.
        """
        with self._database.session() as session:
            return TradeAttributionRepository.upsert(session, row)

    def deployment(self, user_id: str, deployment_id: str) -> dict[str, Any] | None:
        """Read one deployment owned by an account.

        Args:
            user_id: Account that owns the deployment.
            deployment_id: Deployment identifier.

        Returns:
            The deployment row, or ``None`` when it is not found.
        """
        with self._database.session() as session:
            return DeploymentRepository.get(session, deployment_id, user_id)

    def deployments(self, user_id: str) -> list[dict[str, Any]]:
        """Read all deployments owned by an account.

        Args:
            user_id: Account whose deployments are requested.

        Returns:
            The account's deployments.
        """
        with self._database.session() as session:
            return DeploymentRepository.list_for_user(session, user_id)

    def opening_orders(self, user_id: str, strategy_id: str, symbol: str) -> list[dict[str, Any]]:
        """Read candidate opening orders in creation order.

        Args:
            user_id: Account that owns the orders.
            strategy_id: Strategy that raised the orders.
            symbol: Traded instrument symbol.

        Returns:
            Matching order rows, oldest first.
        """
        with self._database.session() as session:
            return OrderRepository.opening_for_strategy(session, user_id, strategy_id, symbol)

    def order_events(self, order_id: str) -> list[dict[str, Any]]:
        """Read one order's status history in event order.

        Args:
            order_id: Order whose event history is requested.

        Returns:
            The order's events.
        """
        with self._database.session() as session:
            return OrderEventRepository.history(session, order_id)

    def signal_context(self, user_id: str, signal_id: str) -> dict[str, Any] | None:
        """Read the context recorded for one account signal.

        Args:
            user_id: Account that owns the context.
            signal_id: Signal whose recorded context is requested.

        Returns:
            The context row, or ``None`` when no row exists.
        """
        with self._database.session() as session:
            return SignalContextRepository.get(session, user_id, signal_id)

    def order(self, user_id: str, order_id: str) -> dict[str, Any] | None:
        """Read one order owned by an account.

        Args:
            user_id: Account that owns the order.
            order_id: Order identifier.

        Returns:
            The order row, or ``None`` when no order exists.
        """
        with self._database.session() as session:
            return OrderRepository.get(session, order_id, user_id)

    def get(self, user_id: str, trade_id: str) -> dict[str, Any] | None:
        """Read one saved attribution for its owner.

        Args:
            user_id: Account that owns the attribution.
            trade_id: Trade whose attribution is requested.

        Returns:
            The saved attribution, or ``None`` when it does not exist.
        """
        with self._database.session() as session:
            return TradeAttributionRepository.get(session, trade_id, user_id)

    def rows(self, user_id: str, filters: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
        """Read a filtered page of an account's attributions.

        Args:
            user_id: Account whose attributions are requested.
            filters: Repository filter names and values.

        Returns:
            The selected rows and the total matching row count.
        """
        with self._database.session() as session:
            return TradeAttributionRepository.list_for_user(session, user_id, **filters)

    def all_rows(self, user_id: str) -> list[dict[str, Any]]:
        """Read all attributions used by account summaries.

        Args:
            user_id: Account whose attributions are requested.

        Returns:
            All stored attributions for the account.
        """
        with self._database.session() as session:
            return TradeAttributionRepository.all_for_user(session, user_id)
