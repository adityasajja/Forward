"""Storage port for post-trade attribution."""

from __future__ import annotations

from typing import Any, Protocol


class AttributionStoragePort(Protocol):
    """Read and write the stored inputs and results for trade attribution."""

    def episodes(
        self, user_id: str, *, deployment_id: str | None, closed_only: bool, limit: int
    ) -> tuple[list[dict[str, Any]], int]:
        """Read journal episodes and their total count for one account.

        Args:
            user_id: Account that owns the episodes.
            deployment_id: Optional deployment filter.
            closed_only: Whether to include only closed episodes.
            limit: Maximum number of rows to return.

        Returns:
            Journal rows and the total count.
        """

    def fingerprint(self, trade_id: str) -> str | None:
        """Return the fingerprint stored for one trade, if present.

        Args:
            trade_id: Trade whose fingerprint is requested.

        Returns:
            The stored fingerprint, or ``None`` when no attribution exists.
        """

    def save(self, row: dict[str, Any]) -> str:
        """Insert or replace a trade attribution.

        Args:
            row: Complete attribution row.

        Returns:
            ``"inserted"`` or ``"replaced"``.
        """

    def deployment(self, user_id: str, deployment_id: str) -> dict[str, Any] | None:
        """Read one account-owned deployment.

        Args:
            user_id: Account that owns the deployment.
            deployment_id: Deployment identifier.

        Returns:
            The deployment row, or ``None`` when it is missing.
        """

    def deployments(self, user_id: str) -> list[dict[str, Any]]:
        """Read all deployments owned by an account.

        Args:
            user_id: Account whose deployments are requested.

        Returns:
            The account's deployment rows.
        """

    def opening_orders(self, user_id: str, strategy_id: str, symbol: str) -> list[dict[str, Any]]:
        """Read candidate opening orders in creation order.

        Args:
            user_id: Account that owns the orders.
            strategy_id: Strategy that raised the orders.
            symbol: Traded instrument.

        Returns:
            Matching orders, oldest first.
        """

    def order_events(self, order_id: str) -> list[dict[str, Any]]:
        """Read one order's status history in event order.

        Args:
            order_id: Order whose events are requested.

        Returns:
            The order's event rows.
        """

    def signal_context(self, user_id: str, signal_id: str) -> dict[str, Any] | None:
        """Read the context recorded for one account signal.

        Args:
            user_id: Account that owns the context.
            signal_id: Signal whose context is requested.

        Returns:
            The recorded context, or ``None`` when it is missing.
        """

    def order(self, user_id: str, order_id: str) -> dict[str, Any] | None:
        """Read one order owned by an account.

        Args:
            user_id: Account that owns the order.
            order_id: Order identifier.

        Returns:
            The order row, or ``None`` when it is missing.
        """

    def get(self, user_id: str, trade_id: str) -> dict[str, Any] | None:
        """Read one saved attribution for its owner.

        Args:
            user_id: Account that owns the attribution.
            trade_id: Trade whose attribution is requested.

        Returns:
            The saved attribution, or ``None`` when it is missing.
        """

    def rows(self, user_id: str, filters: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
        """Read a filtered page of an account's attributions.

        Args:
            user_id: Account whose attributions are requested.
            filters: Repository filters and values.

        Returns:
            Attribution rows and the total count.
        """

    def all_rows(self, user_id: str) -> list[dict[str, Any]]:
        """Read all attributions used by account summaries.

        Args:
            user_id: Account whose attributions are requested.

        Returns:
            All saved attribution rows.
        """
