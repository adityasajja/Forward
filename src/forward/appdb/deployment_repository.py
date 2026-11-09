"""SQL repository for strategy deployment records."""

from __future__ import annotations

from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from forward.appdb.engine import utcnow
from forward.appdb.execution_schema import deployments
from forward.appdb.repository_support import json_text, many, new_id, one


class DeploymentRepository:
    """A running strategy instance: what is live, on what capital, in what mode."""

    @staticmethod
    def create(
        session: Session,
        *,
        user_id: str,
        strategy_id: str,
        strategy_version: int,
        mode: str = "PAPER",
        capital: float,
        broker_account: str | None = None,
        config: dict[str, Any] | None = None,
        status: str = "PENDING",
    ) -> dict[str, Any]:
        mode = (mode or "PAPER").strip().upper()
        if mode not in ("PAPER", "LIVE"):
            raise ValueError(f"mode must be PAPER or LIVE, got {mode!r}")
        if float(capital) <= 0:
            raise ValueError("capital must be positive")
        now = utcnow()
        row = {
            "deployment_id": new_id(),
            "user_id": user_id,
            "strategy_id": strategy_id,
            "strategy_version": int(strategy_version),
            "mode": mode,
            "status": status,
            "capital": float(capital),
            "broker_account": broker_account,
            "config": json_text(config),
            "started_at": None,
            "stopped_at": None,
            "stop_reason": None,
            "created_at": now,
            "updated_at": now,
        }
        session.execute(insert(deployments).values(**row))
        return row

    @staticmethod
    def get(
        session: Session, deployment_id: str, user_id: str
    ) -> dict[str, Any] | None:
        stmt = select(deployments).where(
            deployments.c.deployment_id == deployment_id,
            deployments.c.user_id == user_id,
        )
        return one(session.execute(stmt).first())

    @staticmethod
    def list_for_user(
        session: Session, user_id: str, *, status: str | None = None
    ) -> list[dict[str, Any]]:
        stmt = select(deployments).where(deployments.c.user_id == user_id)
        if status:
            stmt = stmt.where(deployments.c.status == status)
        stmt = stmt.order_by(deployments.c.created_at.desc())
        return many(session.execute(stmt).all())

    @staticmethod
    def recent_for_strategy(
        session: Session,
        strategy_id: str,
        *,
        user_id: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Return recent deployment settings for a strategy."""
        stmt = select(deployments).where(deployments.c.strategy_id == strategy_id)
        if user_id is not None:
            stmt = stmt.where(deployments.c.user_id == user_id)
        stmt = stmt.order_by(deployments.c.created_at.desc()).limit(limit)
        return many(session.execute(stmt).all())

    @staticmethod
    def running(session: Session, user_id: str) -> list[dict[str, Any]]:
        return DeploymentRepository.list_for_user(session, user_id, status="RUNNING")

    @staticmethod
    def running_for_execution(session: Session) -> list[dict[str, Any]]:
        """Return every running paper or live deployment for the execution loop."""
        stmt = select(deployments).where(
            deployments.c.status == "RUNNING",
            deployments.c.mode.in_(("PAPER", "LIVE")),
        )
        return many(session.execute(stmt).all())

    @staticmethod
    def start(session: Session, deployment_id: str, user_id: str) -> int:
        """Start or resume a deployment.

        Only ``PENDING`` and ``PAUSED`` may be started. A **stopped** deployment is
        terminal: resuming it would mean the account's history continues after a
        decision to end it, and "why is this strategy still trading?" would have no
        answer. Restarting a stopped strategy is a new deployment, which is what
        ``DeploymentService.reset`` creates.
        """
        now = utcnow()
        result = session.execute(
            update(deployments)
            .where(
                deployments.c.deployment_id == deployment_id,
                deployments.c.user_id == user_id,
                deployments.c.status.in_(("PENDING", "PAUSED")),
            )
            .values(status="RUNNING", started_at=now, stopped_at=None,
                    stop_reason=None, updated_at=now)
        )
        return int(result.rowcount or 0)

    @staticmethod
    def pause(session: Session, deployment_id: str, user_id: str, *, reason: str) -> int:
        """Pause a running deployment. ``reason`` is mandatory, as for stop.

        Pausing is a *decision about risk*, so it is recorded the same way stopping
        is. Only a running deployment can be paused; the filter is what makes the
        zero row count mean "not running" rather than "not yours".
        """
        reason = (reason or "").strip()
        if not reason:
            raise ValueError("pausing a deployment requires a reason")
        now = utcnow()
        result = session.execute(
            update(deployments)
            .where(
                deployments.c.deployment_id == deployment_id,
                deployments.c.user_id == user_id,
                deployments.c.status == "RUNNING",
            )
            .values(status="PAUSED", stop_reason=reason[:255], updated_at=now)
        )
        return int(result.rowcount or 0)

    @staticmethod
    def stop(session: Session, deployment_id: str, user_id: str, *, reason: str) -> int:
        """Stop a deployment. ``reason`` is mandatory and must say something.

        A deployment that stopped for no recorded reason is a deployment nobody
        can explain afterwards, and "why did this stop trading?" is the first
        question asked when something is wrong.
        """
        reason = (reason or "").strip()
        if not reason:
            raise ValueError("stopping a deployment requires a reason")
        now = utcnow()
        result = session.execute(
            update(deployments)
            .where(
                deployments.c.deployment_id == deployment_id,
                deployments.c.user_id == user_id,
                deployments.c.status != "STOPPED",
            )
            .values(status="STOPPED", stopped_at=now, stop_reason=reason[:255],
                    updated_at=now)
        )
        return int(result.rowcount or 0)
