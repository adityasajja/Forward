"""SQL repository for saved screener definitions."""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.orm import Session

from forward.appdb.engine import utcnow
from forward.appdb.repository_support import json_text, many, new_id, one
from forward.appdb.screener_schema import screener_scans


class ScreenerRepository:
    """Saved screens. The condition tree is opaque here — ``forward.screener`` owns it."""

    @staticmethod
    def create(
        session: Session,
        *,
        user_id: str,
        name: str,
        definition: dict[str, Any] | str,
        description: str | None = None,
    ) -> dict[str, Any]:
        now = utcnow()
        row = {
            "scan_id": new_id(),
            "user_id": user_id,
            "name": name.strip()[:64],
            "description": description,
            "definition": json_text(definition) or "{}",
            "created_at": now,
            "updated_at": now,
        }
        session.execute(insert(screener_scans).values(**row))
        return row

    @staticmethod
    def get(session: Session, scan_id: str, user_id: str) -> dict[str, Any] | None:
        stmt = select(screener_scans).where(
            screener_scans.c.scan_id == scan_id, screener_scans.c.user_id == user_id
        )
        return one(session.execute(stmt).first())

    @staticmethod
    def list_for_user(session: Session, user_id: str) -> list[dict[str, Any]]:
        stmt = (
            select(screener_scans)
            .where(screener_scans.c.user_id == user_id)
            .order_by(screener_scans.c.name)
        )
        return many(session.execute(stmt).all())

    @staticmethod
    def list_all(session: Session) -> list[dict[str, Any]]:
        """Every user's scans — for the watch loop, which serves all of them."""
        return many(session.execute(select(screener_scans)).all())

    @staticmethod
    def name_taken(session: Session, user_id: str, name: str) -> bool:
        stmt = select(func.count()).select_from(screener_scans).where(
            screener_scans.c.user_id == user_id,
            func.lower(screener_scans.c.name) == name.strip().lower(),
        )
        return bool(session.execute(stmt).scalar())

    @staticmethod
    def update(
        session: Session,
        scan_id: str,
        user_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        definition: dict[str, Any] | str | None = None,
    ) -> int:
        values: dict[str, Any] = {}
        if name is not None:
            values["name"] = name.strip()[:64]
        if description is not None:
            values["description"] = description
        if definition is not None:
            values["definition"] = json_text(definition)
        if not values:
            return 0
        values["updated_at"] = utcnow()
        result = session.execute(
            update(screener_scans)
            .where(
                screener_scans.c.scan_id == scan_id,
                screener_scans.c.user_id == user_id,
            )
            .values(**values)
        )
        return int(result.rowcount or 0)

    @staticmethod
    def delete(session: Session, scan_id: str, user_id: str) -> int:
        result = session.execute(
            delete(screener_scans).where(
                screener_scans.c.scan_id == scan_id,
                screener_scans.c.user_id == user_id,
            )
        )
        return int(result.rowcount or 0)
