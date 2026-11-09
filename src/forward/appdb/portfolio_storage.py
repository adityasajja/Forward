"""SQL adapter for portfolio policies, deployments, fills, and trade history."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Any

from sqlalchemy import insert, select, update

from forward.appdb.deployment_repository import DeploymentRepository
from forward.appdb.engine import AppDatabase, utcnow
from forward.appdb.order_event_repository import OrderEventRepository
from forward.appdb.order_repository import OrderRepository
from forward.appdb.portfolio_schema import portfolio_policies
from forward.appdb.strategy_repository import StrategyRepository
from forward.appdb.trade_journal_repository import TradeJournalRepository
from forward.core.portfolio_ports import (
    PortfolioStoragePort,
)
from forward.core.time import timestamp_sort_key


class SqlPortfolioStorage(PortfolioStoragePort):
    """Read and write the portfolio data through the application database."""

    def __init__(self, db: AppDatabase) -> None:
        """Create the adapter for one application database.

        Args:
            db: Database used for portfolio policy and trading records.
        """
        self._db = db

    def policy(self, user_id: str) -> str | None:
        """Return the saved policy JSON for one account.

        Args:
            user_id: Account whose policy is requested.

        Returns:
            Policy JSON, or ``None`` when no policy exists.
        """
        with self._db.session() as session:
            row = session.execute(
                select(portfolio_policies.c.policy).where(
                    portfolio_policies.c.user_id == user_id
                )
            ).first()
        return None if row is None else row[0]

    def save_policy(
        self, user_id: str, policy: str, *, actor: str, reason: str
    ) -> datetime:
        """Replace the user's policy in one database transaction.

        Args:
            user_id: Account that owns the policy.
            policy: Validated policy JSON.
            actor: User or service that made the change.
            reason: Required explanation for changing portfolio risk.

        Returns:
            The time the policy was saved.
        """
        now = utcnow()
        values = {
            "policy": policy,
            "updated_at": now,
            "updated_by": actor[:64] or None,
            "reason": reason,
        }
        with self._db.session() as session:
            exists = session.execute(
                select(portfolio_policies.c.user_id).where(
                    portfolio_policies.c.user_id == user_id
                )
            ).first() is not None
            if exists:
                session.execute(
                    update(portfolio_policies)
                    .where(portfolio_policies.c.user_id == user_id)
                    .values(**values)
                )
            else:
                session.execute(
                    insert(portfolio_policies).values(user_id=user_id, **values)
                )
        return now

    def deployments(self, user_id: str) -> list[dict[str, Any]]:
        """Return the account's deployments.

        Args:
            user_id: Account whose deployments are requested.

        Returns:
            The account's deployment rows.
        """
        with self._db.session() as session:
            return DeploymentRepository.list_for_user(session, user_id)

    def deployment(self, user_id: str, deployment_id: str) -> dict[str, Any] | None:
        """Read one deployment owned by an account.

        Args:
            user_id: Account that must own the deployment.
            deployment_id: Deployment to read.

        Returns:
            The deployment row, or ``None`` when it is not owned by the account.
        """
        with self._db.session() as session:
            return DeploymentRepository.get(session, deployment_id, user_id)

    def fills(self, user_id: str) -> list[dict[str, Any]]:
        """Return each reported cumulative fill as its incremental fill.

        Args:
            user_id: Account whose fills are requested.

        Returns:
            Incremental fill rows in fill-time order.
        """
        with self._db.session() as session:
            orders = {
                row["order_id"]: row
                for row in OrderRepository.for_replay(session, user_id)
            }
            events = OrderEventRepository.fills_for_orders(session, list(orders))

        fills: list[dict[str, Any]] = []
        for order_id, order in orders.items():
            previous = 0.0
            for event in events.get(order_id, []):
                if event.get("filled_qty") is None:
                    continue
                cumulative = float(event["filled_qty"])
                incremental = cumulative - previous
                previous = cumulative
                if incremental <= 0:
                    continue
                if event["filled_price"] is None:
                    raise ValueError(f"fill event for order {order_id} has no filled price")
                if event["fill_ts"] is None:
                    raise ValueError(f"fill event for order {order_id} has no fill timestamp")
                price = float(event["filled_price"])
                commission = (
                    None if event["commission"] is None else float(event["commission"])
                )
                fills.append(
                    {
                        "deployment_id": order.get("deployment_id"),
                        "strategy_id": order.get("strategy_id"),
                        "symbol": order.get("symbol"),
                        "side": order.get("side"),
                        "qty": incremental,
                        "price": price,
                        "commission": commission,
                        "ts": event["fill_ts"],
                    }
                )
        fills.sort(key=lambda fill: timestamp_sort_key(fill.get("ts")))
        return fills

    def iter_journal_episodes(
        self, user_id: str, *, closed_only: bool
    ) -> Iterator[dict[str, Any]]:
        """Stream every journal episode for one account.

        Args:
            user_id: Account whose episodes are requested.
            closed_only: Whether to include only closed episodes.

        Returns:
            Matching journal rows in stable order.
        """
        with self._db.session() as session:
            yield from TradeJournalRepository.iter_for_user(
                session, user_id, closed_only=closed_only
            )

    def journal_episode_count(self, user_id: str, deployment_id: str) -> int:
        """Count closed journal episodes for one account deployment.

        Args:
            user_id: Account that owns the deployment.
            deployment_id: Deployment whose closed episodes are counted.

        Returns:
            Number of matching closed episodes.
        """
        with self._db.session() as session:
            _rows, total = TradeJournalRepository.list_for_user(
                session,
                user_id,
                deployment_id=deployment_id,
                closed_only=True,
                limit=1,
            )
        return total

    def monitoring_journal_pages(
        self, user_id: str, deployment_id: str, limit: int
    ) -> tuple[list[dict[str, Any]], int, list[dict[str, Any]], int]:
        """Read closed and all journal rows in one database session.

        Args:
            user_id: Account that owns the deployment.
            deployment_id: Deployment whose journal is requested.
            limit: Maximum rows in each page.

        Returns:
            Closed rows and count, followed by all rows and count.
        """
        with self._db.session() as session:
            closed_rows, closed_total = TradeJournalRepository.list_for_user(
                session,
                user_id,
                deployment_id=deployment_id,
                closed_only=True,
                limit=limit,
            )
            all_rows, all_total = TradeJournalRepository.list_for_user(
                session,
                user_id,
                deployment_id=deployment_id,
                closed_only=False,
                limit=limit,
            )
        return closed_rows, closed_total, all_rows, all_total

    def strategies(self, user_id: str) -> list[dict[str, Any]]:
        """Return saved strategies for display names.

        Args:
            user_id: Account whose strategies are requested.

        Returns:
            Matching strategy rows.
        """
        with self._db.session() as session:
            return StrategyRepository.list_for_user(session, user_id)
