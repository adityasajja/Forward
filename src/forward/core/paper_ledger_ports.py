"""Ports for paper ledger."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from forward.core.order_models import Fill


class PaperRunnerLedgerPort(Protocol):
    """Read the paper book used by deployment evaluation."""

    def portfolio(
        self,
        user_id: str,
        *,
        deployment_id: str | None = None,
        initial_cash: float | None = None,
        prices: dict[str, float] | None = None,
        mode: str | None = None,
    ) -> Any:
        """Replay the paper book for one account.

        Args:

            user_id: Account whose fills are replayed.

            deployment_id: Optional deployment filter.

            initial_cash: Optional starting cash for the replay.

            prices: Optional marks used to value open positions.

            mode: Optional execution-mode filter.

        Returns:

            The replayed portfolio.
        """

    def snapshot(
        self,
        user_id: str,
        *,
        deployment_id: str | None = None,
        initial_cash: float | None = None,
        prices: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """Return a JSON-ready paper-book snapshot.

        Args:

            user_id: Account whose book is read.

            deployment_id: Optional deployment filter.

            initial_cash: Optional starting cash for the replay.

            prices: Optional marks used to value open positions.

        Returns:

            Current positions, cash, and P&L.
        """


class PaperDeploymentLedgerPort(Protocol):
    """Read account snapshots for paper deployment monitoring."""

    def snapshots_for_deployments(
        self, user_id: str, deployments: list[dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        """Read one valued snapshot for each deployment.

        Args:

            user_id: Account that owns the deployments.

            deployments: Deployment records to value.

        Returns:

            Snapshots keyed by deployment identifier.
        """

    def snapshot(
        self,
        user_id: str,
        *,
        deployment_id: str | None = None,
        initial_cash: float | None = None,
        prices: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """Read one paper account snapshot.

        Args:
            user_id: Account whose book is read.
            deployment_id: Optional deployment filter.
            initial_cash: Optional starting cash for an ad-hoc account.
            prices: Optional marks used to value open positions.

        Returns:
            Positions, cash, and P&L for the selected account.
        """


class PaperMonitoringLedgerPort(Protocol):
    """Read a deployment's fills and account snapshots for monitoring."""

    def snapshot(self, user_id: str, *, deployment_id: str) -> dict[str, Any]:
        """Return the current account book for one deployment.

        Args:
            user_id: Account that owns the deployment.
            deployment_id: Deployment whose book should be read.

        Returns:
            Positions, cash, and P&L for the deployment.
        """

    def snapshot_and_fills(
        self,
        user_id: str,
        *,
        deployment_id: str,
        prices: dict[str, float] | None = None,
    ) -> tuple[dict[str, Any], list[Fill]]:
        """Return one valued snapshot and the fills used to build it.

        Args:
            user_id: Account that owns the deployment.
            deployment_id: Deployment whose book should be read.
            prices: Optional current marks for open positions.

        Returns:
            The account snapshot and its fills.
        """

    def fills(self, user_id: str, *, deployment_id: str) -> list[Fill]:
        """Return the ordered fills for one deployment.

        Args:
            user_id: Account that owns the deployment.
            deployment_id: Deployment whose fills should be read.

        Returns:
            Fills in execution-time order.
        """


class PaperLedgerStoragePort(Protocol):
    """Read stored paper executions for account replay."""

    def deployment_cash(self, user_id: str, deployment_id: str) -> float:
        """Return the starting cash assigned to one owned deployment.

        Args:

            user_id: Account that owns the deployment.

            deployment_id: Deployment whose capital is requested.

        Returns:

            The saved capital allocation.
        """

    def fills(self, user_id: str, *, deployment_id: str | None, mode: str | None) -> list[Fill]:
        """Read all incremental fills for an account.

        Args:

            user_id: Account whose executions are requested.

            deployment_id: Optional deployment filter.

            mode: Optional execution-mode filter.

        Returns:

            Fills in execution-time order.
        """

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

    def activity(self, user_id: str, *, limit: int) -> dict[str, Any]:
        """Read recent paper orders and their total count.

        Args:

            user_id: Account whose paper activity is requested.

            limit: Maximum number of orders to return.

        Returns:

            Order rows and the total matching row count.
        """


class PaperFillLedgerPort(Protocol):
    """Read the fills used by paper-account reporting."""

    def fills(self, user_id: str, *, deployment_id: str | None = None) -> Sequence[Any]:
        """Return incremental paper fills for one account.

        Args:
            user_id: Account whose fills are requested.
            deployment_id: Optional deployment filter.

        Returns:
            The account's incremental fills.
        """
