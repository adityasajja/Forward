"""SQL repository for user-owned watchlists and their contents."""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.orm import Session

from forward.appdb.engine import utcnow
from forward.appdb.repository_support import many, new_id, one
from forward.appdb.watchlists_schema import watchlist_columns, watchlist_items, watchlists
from forward.core.errors import InvalidWatchlistOrder


# ---------------------------------------------------------------------- watchlists
class WatchlistRepository:
    """Store user-owned watchlists, items, and column settings."""

    @staticmethod
    def create(
        session: Session,
        *,
        user_id: str,
        name: str,
        exchange: str = "NSEEQ",
        columns: list[str] | None = None,
    ) -> dict[str, Any]:
        now = utcnow()
        existing = WatchlistRepository.list_for_user(session, user_id)
        row = {
            "watchlist_id": new_id(),
            "user_id": user_id,
            "name": name.strip()[:64],
            "exchange": exchange,
            # The first list a user makes becomes their default, so "add to
            # watchlist" has an answer before the user has expressed a preference.
            "is_default": len(existing) == 0,
            "sort_order": len(existing),
            "created_at": now,
            "updated_at": now,
        }
        session.execute(insert(watchlists).values(**row))
        if columns:
            WatchlistRepository.set_columns(session, row["watchlist_id"], columns)
        return row

    @staticmethod
    def list_for_user(session: Session, user_id: str) -> list[dict[str, Any]]:
        stmt = (
            select(watchlists)
            .where(watchlists.c.user_id == user_id)
            .order_by(watchlists.c.sort_order, watchlists.c.created_at)
        )
        rows = many(session.execute(stmt).all())
        for row in rows:
            row["item_count"] = WatchlistRepository.item_count(session, row["watchlist_id"])
        return rows

    @staticmethod
    def get(session: Session, watchlist_id: str, user_id: str) -> dict[str, Any] | None:
        stmt = select(watchlists).where(
            watchlists.c.watchlist_id == watchlist_id, watchlists.c.user_id == user_id
        )
        return one(session.execute(stmt).first())

    @staticmethod
    def update(session: Session, watchlist_id: str, user_id: str, **fields: Any) -> int:
        allowed = {"name", "exchange", "is_default", "sort_order"}
        values = {k: v for k, v in fields.items() if k in allowed}
        if not values:
            return 0
        if "name" in values and values["name"]:
            values["name"] = str(values["name"]).strip()[:64]
        values["updated_at"] = utcnow()
        result = session.execute(
            update(watchlists)
            .where(watchlists.c.watchlist_id == watchlist_id, watchlists.c.user_id == user_id)
            .values(**values)
        )
        return int(result.rowcount or 0)

    @staticmethod
    def delete(session: Session, watchlist_id: str, user_id: str) -> int:
        result = session.execute(
            delete(watchlists).where(
                watchlists.c.watchlist_id == watchlist_id, watchlists.c.user_id == user_id
            )
        )
        return int(result.rowcount or 0)

    @staticmethod
    def set_default(session: Session, watchlist_id: str, user_id: str) -> int:
        """Exactly one default per user, set in one transaction."""
        session.execute(
            update(watchlists).where(watchlists.c.user_id == user_id).values(is_default=False)
        )
        result = session.execute(
            update(watchlists)
            .where(watchlists.c.watchlist_id == watchlist_id, watchlists.c.user_id == user_id)
            .values(is_default=True, updated_at=utcnow())
        )
        return int(result.rowcount or 0)

    # ------------------------------------------------------------------ items
    @staticmethod
    def item_count(session: Session, watchlist_id: str) -> int:
        stmt = (
            select(func.count())
            .select_from(watchlist_items)
            .where(watchlist_items.c.watchlist_id == watchlist_id)
        )
        return int(session.execute(stmt).scalar() or 0)

    @staticmethod
    def items(session: Session, watchlist_id: str) -> list[dict[str, Any]]:
        stmt = (
            select(watchlist_items)
            .where(watchlist_items.c.watchlist_id == watchlist_id)
            .order_by(watchlist_items.c.sort_order, watchlist_items.c.added_at)
        )
        return many(session.execute(stmt).all())

    @staticmethod
    def add_items(
        session: Session, watchlist_id: str, symbols: list[str]
    ) -> tuple[list[str], list[str]]:
        """Append symbols, skipping ones already present.

        Returns ``(added, skipped)``. Duplicates are reported rather than raising
        so a multi-symbol paste is not all-or-nothing: adding five names of which
        one is already there should add four.
        """
        existing = {row["symbol"] for row in WatchlistRepository.items(session, watchlist_id)}
        next_order = (
            int(
                session.execute(
                    select(func.max(watchlist_items.c.sort_order)).where(
                        watchlist_items.c.watchlist_id == watchlist_id
                    )
                ).scalar()
                or -1
            )
            + 1
        )
        added: list[str] = []
        skipped: list[str] = []
        now = utcnow()
        for symbol in symbols:
            clean = symbol.strip().upper()
            if not clean or clean in existing:
                skipped.append(clean)
                continue
            session.execute(
                insert(watchlist_items).values(
                    item_id=new_id(),
                    watchlist_id=watchlist_id,
                    symbol=clean,
                    sort_order=next_order,
                    note=None,
                    added_at=now,
                )
            )
            existing.add(clean)
            added.append(clean)
            next_order += 1
        return added, skipped

    @staticmethod
    def remove_item(session: Session, watchlist_id: str, symbol: str) -> int:
        result = session.execute(
            delete(watchlist_items).where(
                watchlist_items.c.watchlist_id == watchlist_id,
                watchlist_items.c.symbol == symbol.strip().upper(),
            )
        )
        return int(result.rowcount or 0)

    @staticmethod
    def reorder_items(session: Session, watchlist_id: str, symbols: list[str]) -> int:
        """Rewrite ``sort_order`` from the given sequence.

        The complete symbol list is required. This makes the call idempotent and
        prevents a partial or duplicate list from being treated as a valid order.
        """
        current = WatchlistRepository.items(session, watchlist_id)
        by_symbol = {row["symbol"]: row for row in current}
        ordered = [symbol.strip().upper() for symbol in symbols]
        if len(ordered) != len(by_symbol) or set(ordered) != set(by_symbol):
            raise InvalidWatchlistOrder(
                "order must include every watchlist symbol exactly once"
            )
        updated = 0
        for index, symbol in enumerate(ordered):
            row = by_symbol[symbol]
            if row["sort_order"] == index:
                continue
            session.execute(
                update(watchlist_items)
                .where(watchlist_items.c.item_id == row["item_id"])
                .values(sort_order=index)
            )
            updated += 1
        return updated

    # ---------------------------------------------------------------- columns
    @staticmethod
    def columns(session: Session, watchlist_id: str) -> list[dict[str, Any]]:
        stmt = (
            select(watchlist_columns)
            .where(watchlist_columns.c.watchlist_id == watchlist_id)
            .order_by(watchlist_columns.c.sort_order)
        )
        return many(session.execute(stmt).all())

    @staticmethod
    def set_columns(session: Session, watchlist_id: str, keys: list[str]) -> list[dict[str, Any]]:
        """Replace the column configuration wholesale."""
        session.execute(delete(watchlist_columns).where(watchlist_columns.c.watchlist_id == watchlist_id))
        seen: set[str] = set()
        for index, key in enumerate(keys):
            clean = key.strip()
            if not clean or clean in seen:
                continue
            seen.add(clean)
            session.execute(
                insert(watchlist_columns).values(
                    column_id=new_id(),
                    watchlist_id=watchlist_id,
                    key=clean,
                    sort_order=index,
                    width=None,
                    visible=True,
                )
            )
        return WatchlistRepository.columns(session, watchlist_id)
