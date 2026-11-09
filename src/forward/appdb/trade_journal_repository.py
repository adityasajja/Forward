"""SQL repository for trade-journal records."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Any

from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session

from forward.appdb.engine import utcnow
from forward.appdb.repository_support import many, new_id, one
from forward.appdb.trade_schema import trade_journal


def _reason_code(reason: str) -> str:
    """Return the category at the start of an exit reason.

    Args:
        reason: Exit reason with a category and optional detail.

    Returns:
        The text before the first colon, limited to 32 characters.
    """
    return str(reason).split(":", 1)[0].strip()[:32]


class TradeJournalRepository:
    """Closed and open trades, with the path statistics a journal exists for."""

    @staticmethod
    def open_trade(
        session: Session,
        *,
        user_id: str,
        symbol: str,
        side: str,
        quantity: float,
        entry_price: float,
        entry_ts: datetime | None = None,
        deployment_id: str | None = None,
        strategy_id: str | None = None,
        strategy_version: int | None = None,
        asset_class: str = "EQUITY",
        signal_reason: str | None = None,
        evidence_grade: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Open an episode.

        ``evidence_grade`` is the provenance verdict the caller established from
        the opening order's event log: ``forward`` when the order was raised
        live, ``in_sample`` otherwise. It is left ``None`` when the caller has no
        verdict to give, and a ``None`` grade is graded in-sample downstream —
        an unknown provenance is not a licence to claim independence.
        """
        row = {
            "trade_id": new_id(),
            "user_id": user_id,
            "deployment_id": deployment_id,
            "strategy_id": strategy_id,
            "strategy_version": strategy_version,
            "symbol": symbol.strip().upper(),
            "asset_class": asset_class,
            "side": (side or "").strip().upper(),
            "quantity": float(quantity),
            "entry_ts": entry_ts or utcnow(),
            "exit_ts": None,
            "entry_price": float(entry_price),
            "exit_price": None,
            "gross_pnl": None,
            "net_pnl": None,
            "mfe": None,
            "mae": None,
            "duration_sec": None,
            "regime": None,
            "signal_reason": signal_reason,
            "slippage_bps": None,
            "evidence_grade": evidence_grade,
            "notes": notes,
            "created_at": utcnow(),
        }
        session.execute(insert(trade_journal).values(**row))
        return row

    @staticmethod
    def close_trade(
        session: Session,
        trade_id: str,
        user_id: str,
        *,
        exit_price: float,
        gross_pnl: float,
        net_pnl: float | None = None,
        exit_ts: datetime | None = None,
        mfe: float | None = None,
        mae: float | None = None,
        slippage_bps: float | None = None,
        regime: str | None = None,
        notes: str | None = None,
    ) -> int:
        """Close a trade. ``duration_sec`` is derived from the timestamps here.

        Derived rather than passed in so the two cannot disagree; the caller
        already had to supply ``exit_ts``.
        """
        trade = TradeJournalRepository.get(session, trade_id, user_id)
        if trade is None:
            return 0
        exit_ts = exit_ts or utcnow()
        entry_ts = trade["entry_ts"]
        duration = None
        if entry_ts is not None:
            duration = int(max(0.0, (exit_ts - entry_ts).total_seconds()))
        values: dict[str, Any] = {
            "exit_price": float(exit_price),
            "exit_ts": exit_ts,
            "gross_pnl": float(gross_pnl),
            "net_pnl": float(net_pnl) if net_pnl is not None else None,
            "duration_sec": duration,
        }
        if mfe is not None:
            values["mfe"] = float(mfe)
        if mae is not None:
            values["mae"] = float(mae)
        if slippage_bps is not None:
            values["slippage_bps"] = float(slippage_bps)
        if regime is not None:
            values["regime"] = regime
        if notes is not None:
            values["notes"] = notes
        result = session.execute(
            update(trade_journal)
            .where(
                trade_journal.c.trade_id == trade_id,
                trade_journal.c.user_id == user_id,
                trade_journal.c.exit_ts.is_(None),
            )
            .values(**values)
        )
        return int(result.rowcount or 0)

    @staticmethod
    def set_exit_reason(
        session: Session, trade_id: str, user_id: str, exit_reason: str
    ) -> int:
        """Record why a closed trade ended.

        Separate from :meth:`close_trade` because the reason lives on the closing
        *order's* event log, not on the journal row, and the close is what makes
        the trade exist. Reading the reason first and then writing the trade
        would mean a missing event loses the P&L to protect a label; this way a
        missing event costs only the label.

        Deliberately unrestricted by ``exit_ts IS NULL`` — this is a correction
        to a field on an already-closed trade, not a second close.
        """
        result = session.execute(
            update(trade_journal)
            .where(
                trade_journal.c.trade_id == trade_id,
                trade_journal.c.user_id == user_id,
            )
            .values(exit_reason=_reason_code(exit_reason), exit_detail=str(exit_reason))
        )
        return int(result.rowcount or 0)

    @staticmethod
    def get(session: Session, trade_id: str, user_id: str) -> dict[str, Any] | None:
        stmt = select(trade_journal).where(
            trade_journal.c.trade_id == trade_id, trade_journal.c.user_id == user_id
        )
        return one(session.execute(stmt).first())

    @staticmethod
    def list_for_user(
        session: Session,
        user_id: str,
        *,
        deployment_id: str | None = None,
        strategy_id: str | None = None,
        symbol: str | None = None,
        closed_only: bool = False,
        limit: int | None = 100,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """Read journal rows for one account and return the total row count.

        Args:
            session: Active database session.
            user_id: Account that owns the journal rows.
            deployment_id: Optional deployment filter.
            strategy_id: Optional strategy filter.
            symbol: Optional symbol filter.
            closed_only: Whether to return closed trades only.
            limit: Maximum rows to return, or ``None`` to read every match.
            offset: Number of matching rows to skip.

        Returns:
            Journal rows and the total number of matches.
        """
        conditions = [trade_journal.c.user_id == user_id]
        if deployment_id:
            conditions.append(trade_journal.c.deployment_id == deployment_id)
        if strategy_id:
            conditions.append(trade_journal.c.strategy_id == strategy_id)
        if symbol:
            conditions.append(trade_journal.c.symbol == symbol.strip().upper())
        if closed_only:
            conditions.append(trade_journal.c.exit_ts.is_not(None))
        total = int(
            session.execute(
                select(func.count()).select_from(trade_journal).where(*conditions)
            ).scalar()
            or 0
        )
        stmt = (
            select(trade_journal)
            .where(*conditions)
            .order_by(trade_journal.c.entry_ts.desc())
            .offset(max(0, offset))
        )
        if limit is not None:
            stmt = stmt.limit(max(1, min(limit, 1000)))
        return many(session.execute(stmt).all()), total

    @staticmethod
    def iter_for_user(
        session: Session,
        user_id: str,
        *,
        closed_only: bool = False,
    ) -> Iterator[dict[str, Any]]:
        """Stream every journal row for one account in a stable order.

        Args:
            session: Active database session held for the full iteration.
            user_id: Account that owns the journal rows.
            closed_only: Whether to include only closed trades.

        Yields:
            Journal rows ordered by entry time and trade ID.
        """
        conditions = [trade_journal.c.user_id == user_id]
        if closed_only:
            conditions.append(trade_journal.c.exit_ts.is_not(None))
        statement = (
            select(trade_journal)
            .where(*conditions)
            .order_by(trade_journal.c.entry_ts.desc(), trade_journal.c.trade_id.desc())
            .execution_options(yield_per=1000)
        )
        for row in session.execute(statement).mappings():
            yield dict(row)
