"""SQL repository for strategies and immutable strategy versions."""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from forward.appdb.engine import utcnow
from forward.appdb.execution_schema import deployments, order_intents, orders
from forward.appdb.repository_support import many, new_id, one
from forward.appdb.research_schema import (
    learning_observations,
    optimization_recommendations,
    strategy_experiments,
)
from forward.appdb.strategy_backtest_schema import backtest_runs, strategies, strategy_versions
from forward.appdb.trade_schema import signal_contexts, trade_attributions, trade_journal
from forward.core.errors import DuplicateDefinition
from forward.core.strategy_kinds import STRATEGY_KINDS
from forward.strategy.definition_identity import canonical_definition, definition_hash


class StrategyRepository:
    """Strategies and their immutable versions.

    Immutability of ``(strategy_id, version)`` is enforced two ways: the primary
    key makes a second row for the same version impossible, and there is simply
    no method here that updates ``definition``. Metadata — name, description —
    is editable, because renaming a strategy does not change what it does.
    """

    @staticmethod
    def create(
        session: Session,
        *,
        user_id: str,
        name: str,
        kind: str,
        description: str | None = None,
        engine_key: str | None = None,
    ) -> dict[str, Any]:
        kind = (kind or "").strip().lower()
        if kind not in STRATEGY_KINDS:
            raise ValueError(
                f"kind must be one of {sorted(STRATEGY_KINDS)}, got {kind!r}"
            )
        now = utcnow()
        row = {
            "strategy_id": new_id(),
            "user_id": user_id,
            "name": name.strip()[:128],
            "description": description,
            "kind": kind,
            "engine_key": engine_key,
            "created_at": now,
            "updated_at": now,
            "archived_at": None,
        }
        session.execute(insert(strategies).values(**row))
        return row

    @staticmethod
    def get(session: Session, strategy_id: str, user_id: str) -> dict[str, Any] | None:
        stmt = select(strategies).where(
            strategies.c.strategy_id == strategy_id, strategies.c.user_id == user_id
        )
        return one(session.execute(stmt).first())

    @staticmethod
    def list_for_user(
        session: Session, user_id: str, *, include_archived: bool = False,
        include_version_summary: bool = True
    ) -> list[dict[str, Any]]:
        stmt = select(strategies).where(strategies.c.user_id == user_id)
        if not include_archived:
            stmt = stmt.where(strategies.c.archived_at.is_(None))
        stmt = stmt.order_by(strategies.c.name)
        rows = many(session.execute(stmt).all())
        if include_version_summary:
            summaries = StrategyRepository.version_summaries(
                session, [row["strategy_id"] for row in rows]
            )
            for row in rows:
                latest, count = summaries.get(row["strategy_id"], (None, 0))
                row["latest_version"] = None if latest is None else latest["version"]
                row["version_count"] = count
        return rows

    @staticmethod
    def name_taken(session: Session, user_id: str, name: str) -> bool:
        stmt = select(func.count()).select_from(strategies).where(
            strategies.c.user_id == user_id,
            func.lower(strategies.c.name) == name.strip().lower(),
        )
        return bool(session.execute(stmt).scalar())

    @staticmethod
    def update_meta(
        session: Session, strategy_id: str, user_id: str, **fields: Any
    ) -> int:
        """Rename / re-describe. Only these two fields are accepted."""
        allowed = {"name", "description"}
        values = {k: v for k, v in fields.items() if k in allowed}
        if not values:
            return 0
        if values.get("name"):
            values["name"] = str(values["name"]).strip()[:128]
        values["updated_at"] = utcnow()
        result = session.execute(
            update(strategies)
            .where(
                strategies.c.strategy_id == strategy_id,
                strategies.c.user_id == user_id,
            )
            .values(**values)
        )
        return int(result.rowcount or 0)

    @staticmethod
    def archive(session: Session, strategy_id: str, user_id: str) -> int:
        """Archive rather than delete: versions and runs reference this row."""
        now = utcnow()
        result = session.execute(
            update(strategies)
            .where(
                strategies.c.strategy_id == strategy_id,
                strategies.c.user_id == user_id,
                strategies.c.archived_at.is_(None),
            )
            .values(archived_at=now, updated_at=now)
        )
        return int(result.rowcount or 0)

    @staticmethod
    def unarchive(session: Session, strategy_id: str, user_id: str) -> int:
        result = session.execute(
            update(strategies)
            .where(
                strategies.c.strategy_id == strategy_id,
                strategies.c.user_id == user_id,
            )
            .values(archived_at=None, updated_at=utcnow())
        )
        return int(result.rowcount or 0)

    @staticmethod
    def active_deployment_count(
        session: Session, user_id: str, strategy_id: str, version: int | None
    ) -> int:
        """Count active deployments using a strategy or exact version.

        Args:
            session: The caller's database transaction.
            user_id: The owner of the strategy.
            strategy_id: The strategy to inspect.
            version: Optional version restriction.

        Returns:
            The number of active deployments.
        """
        stmt = select(func.count()).select_from(deployments).where(
            deployments.c.user_id == user_id,
            deployments.c.strategy_id == strategy_id,
            deployments.c.status.in_(("RUNNING", "PENDING")),
        )
        if version is not None:
            stmt = stmt.where(deployments.c.strategy_version == version)
        return int(session.execute(stmt).scalar_one())

    @staticmethod
    def delete_history(session: Session, strategy_id: str, version: int | None) -> None:
        """Delete history rows created by a strategy or exact version.

        Args:
            session: The caller's database transaction.
            strategy_id: The strategy whose history should be removed.
            version: Optional version restriction.
        """
        def matching(table: Any, version_column: Any = None) -> Any:
            condition = table.c.strategy_id == strategy_id
            if version is not None:
                column = version_column if version_column is not None else table.c.strategy_version
                condition = condition & (column == version)
            return condition

        run_ids = select(deployments.c.deployment_id).where(matching(deployments))
        session.execute(
            delete(trade_attributions).where(trade_attributions.c.deployment_id.in_(run_ids))
        )
        owned_tables = (
            order_intents,
            orders,
            trade_journal,
            signal_contexts,
            learning_observations,
            backtest_runs,
            deployments,
        )
        for table in owned_tables:
            session.execute(delete(table).where(matching(table)))
        session.execute(
            delete(optimization_recommendations).where(
                matching(
                    optimization_recommendations,
                    optimization_recommendations.c.source_strategy_version,
                )
            )
        )
        session.execute(
            delete(strategy_experiments).where(
                matching(strategy_experiments, strategy_experiments.c.source_version)
            )
        )

    @staticmethod
    def delete(session: Session, strategy_id: str) -> int:
        """Delete a strategy row after its dependent history has been removed."""
        result = session.execute(delete(strategies).where(strategies.c.strategy_id == strategy_id))
        return int(result.rowcount or 0)

    @staticmethod
    def delete_version(session: Session, strategy_id: str, version: int) -> int:
        """Delete one immutable version row after its dependent history is removed."""
        result = session.execute(
            delete(strategy_versions).where(
                strategy_versions.c.strategy_id == strategy_id,
                strategy_versions.c.version == version,
            )
        )
        return int(result.rowcount or 0)

    # --------------------------------------------------------------- versions
    @staticmethod
    def create_version(
        session: Session,
        *,
        strategy_id: str,
        author_user_id: str,
        definition: dict[str, Any] | str,
        change_note: str | None = None,
    ) -> dict[str, Any]:
        """Append a new immutable version. Never updates an existing one.

        ``author_user_id`` is checked against the strategy's owner rather than
        trusted: the FK proves the strategy exists, not that this caller may add
        to it.
        """
        owner = session.execute(
            select(strategies.c.user_id).where(strategies.c.strategy_id == strategy_id)
        ).scalar()
        if owner is None:
            raise LookupError(f"no strategy {strategy_id}")
        if owner != author_user_id:
            raise PermissionError("strategy belongs to another account")

        canonical = canonical_definition(definition)
        digest = definition_hash(canonical)
        duplicate = session.execute(
            select(strategy_versions.c.version)
            .where(
                strategy_versions.c.strategy_id == strategy_id,
                strategy_versions.c.definition_hash == digest,
            )
            .limit(1)
        ).scalar()
        if duplicate is not None:
            raise DuplicateDefinition(int(duplicate))

        for attempt in range(2):
            version = (
                int(
                    session.execute(
                        select(func.max(strategy_versions.c.version)).where(
                            strategy_versions.c.strategy_id == strategy_id
                        )
                    ).scalar()
                    or 0
                )
                + 1
            )
            row = {
                "strategy_id": strategy_id,
                "version": version,
                "author_user_id": author_user_id,
                "created_at": utcnow(),
                "definition": canonical,
                "definition_hash": digest,
                "change_note": change_note,
                "is_deployed": False,
            }
            try:
                with session.begin_nested():
                    session.execute(insert(strategy_versions).values(**row))
                return row
            except IntegrityError:
                if attempt == 1:
                    raise
                continue
        raise RuntimeError("unreachable")  # pragma: no cover

    @staticmethod
    def versions(session: Session, strategy_id: str) -> list[dict[str, Any]]:
        stmt = (
            select(strategy_versions)
            .where(strategy_versions.c.strategy_id == strategy_id)
            .order_by(strategy_versions.c.version.desc())
        )
        return many(session.execute(stmt).all())

    @staticmethod
    def versions_for_user(
        session: Session, strategy_id: str, user_id: str
    ) -> list[dict[str, Any]]:
        """Return versions only when the requested account owns the strategy.

        Args:
            session: Open database session.
            strategy_id: Strategy whose versions are requested.
            user_id: Account that must own the strategy.

        Returns:
            Strategy versions, newest first, or an empty list when not owned.
        """
        stmt = (
            select(strategy_versions)
            .join(strategies, strategies.c.strategy_id == strategy_versions.c.strategy_id)
            .where(
                strategy_versions.c.strategy_id == strategy_id,
                strategies.c.user_id == user_id,
            )
            .order_by(strategy_versions.c.version.desc())
        )
        return many(session.execute(stmt).all())

    @staticmethod
    def version(
        session: Session, strategy_id: str, version: int
    ) -> dict[str, Any] | None:
        stmt = select(strategy_versions).where(
            strategy_versions.c.strategy_id == strategy_id,
            strategy_versions.c.version == int(version),
        )
        return one(session.execute(stmt).first())

    @staticmethod
    def version_for_user(
        session: Session, strategy_id: str, version: int, user_id: str
    ) -> dict[str, Any] | None:
        """Return an exact version only when the account owns its strategy.

        Args:
            session: Open database session.
            strategy_id: Strategy that owns the version.
            version: Exact version number.
            user_id: Account that must own the strategy.

        Returns:
            The version row, or ``None`` when it is missing or not owned.
        """
        stmt = (
            select(strategy_versions)
            .join(strategies, strategies.c.strategy_id == strategy_versions.c.strategy_id)
            .where(
                strategy_versions.c.strategy_id == strategy_id,
                strategy_versions.c.version == int(version),
                strategies.c.user_id == user_id,
            )
        )
        return one(session.execute(stmt).first())

    @staticmethod
    def versions_for_keys(
        session: Session, keys: list[tuple[str, int]]
    ) -> dict[tuple[str, int], dict[str, Any]]:
        """Read exact deployed versions in bounded batches, never substituting latest."""
        from sqlalchemy import tuple_

        unique = list(dict.fromkeys(keys))
        result: dict[tuple[str, int], dict[str, Any]] = {}
        for start in range(0, len(unique), 400):
            rows = session.execute(
                select(strategy_versions).where(
                    tuple_(strategy_versions.c.strategy_id, strategy_versions.c.version).in_(
                        unique[start : start + 400]
                    )
                )
            ).mappings()
            for row in rows:
                result[(row["strategy_id"], row["version"])] = dict(row)
        return result

    @staticmethod
    def latest_version(
        session: Session, strategy_id: str
    ) -> dict[str, Any] | None:
        stmt = (
            select(strategy_versions)
            .where(strategy_versions.c.strategy_id == strategy_id)
            .order_by(strategy_versions.c.version.desc())
            .limit(1)
        )
        return one(session.execute(stmt).first())

    @staticmethod
    def version_summaries(
        session: Session, strategy_ids: list[str]
    ) -> dict[str, tuple[dict[str, Any], int]]:
        """Read only latest definitions and counts, in bounded batches."""
        result: dict[str, tuple[dict[str, Any], int]] = {}
        for start in range(0, len(strategy_ids), 500):
            summary = (
                select(
                    strategy_versions.c.strategy_id.label("sid"),
                    func.max(strategy_versions.c.version).label("latest"),
                    func.count().label("version_count"),
                )
                .where(strategy_versions.c.strategy_id.in_(strategy_ids[start:start + 500]))
                .group_by(strategy_versions.c.strategy_id)
                .subquery()
            )
            stmt = select(strategy_versions, summary.c.version_count).join(
                summary,
                (strategy_versions.c.strategy_id == summary.c.sid)
                & (strategy_versions.c.version == summary.c.latest),
            )
            for row in session.execute(stmt).mappings():
                data = dict(row)
                count = int(data.pop("version_count"))
                result[data["strategy_id"]] = (data, count)
        return result

    @staticmethod
    def version_count(session: Session, strategy_id: str) -> int:
        stmt = select(func.count()).select_from(strategy_versions).where(
            strategy_versions.c.strategy_id == strategy_id
        )
        return int(session.execute(stmt).scalar() or 0)

    @staticmethod
    def mark_deployed(
        session: Session, strategy_id: str, version: int, *, deployed: bool = True
    ) -> int:
        """Flag whether a version is (or has been) deployed. Not a definition change."""
        result = session.execute(
            update(strategy_versions)
            .where(
                strategy_versions.c.strategy_id == strategy_id,
                strategy_versions.c.version == int(version),
            )
            .values(is_deployed=bool(deployed))
        )
        return int(result.rowcount or 0)
