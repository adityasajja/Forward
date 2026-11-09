"""SQL repository for durable installation state."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from forward.appdb.engine import utcnow
from forward.appdb.repository_support import many, one
from forward.appdb.system_schema import system_state


class SystemStateRepository:
    """Key/value state that belongs to the installation rather than to a user.

    Two things live here and both are safety-critical: the kill switch and the
    execution mode. They used to be an in-process dict, which meant a restart
    silently re-armed trading and dropped the platform back to paper mode — a
    safety decision undone by a crash. Durable storage is the fix.
    """

    @staticmethod
    def get(session: Session, key: str) -> dict[str, Any] | None:
        stmt = select(system_state).where(system_state.c.key == key)
        return one(session.execute(stmt).first())

    @staticmethod
    def get_value(session: Session, key: str, default: Any = None) -> Any:
        """Return the JSON value, or ``default`` when the key is not set."""
        row = SystemStateRepository.get(session, key)
        if row is None or row["value"] is None:
            return default
        return json.loads(row["value"])

    @staticmethod
    def set(
        session: Session,
        key: str,
        value: Any,
        *,
        actor: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Upsert one key. ``reason`` is stored verbatim; the caller decides if it is required."""
        payload = json.dumps(value, default=str)
        now = utcnow()
        existing = SystemStateRepository.get(session, key)
        if existing is None:
            session.execute(
                insert(system_state).values(
                    key=key,
                    value=payload,
                    updated_at=now,
                    updated_by=(actor or "")[:64] or None,
                    reason=reason,
                )
            )
        else:
            session.execute(
                update(system_state)
                .where(system_state.c.key == key)
                .values(
                    value=payload,
                    updated_at=now,
                    updated_by=(actor or "")[:64] or None,
                    reason=reason,
                )
            )
        return {
            "key": key,
            "value": payload,
            "updated_at": now,
            "updated_by": actor,
            "reason": reason,
        }

    @staticmethod
    def all(session: Session) -> dict[str, Any]:
        """Return every key with its decoded JSON value.

        Raises:
            json.JSONDecodeError: If a stored value is not valid JSON.
        """
        out: dict[str, Any] = {}
        for row in many(session.execute(select(system_state)).all()):
            value = row["value"]
            out[row["key"]] = json.loads(value) if value is not None else None
        return out


# --------------------------------------------------------------------------- learning_observations
