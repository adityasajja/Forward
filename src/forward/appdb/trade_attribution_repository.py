"""SQL repository for post-trade attribution records."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session

from forward.appdb.engine import utcnow
from forward.appdb.repository_support import decode_json_fields
from forward.appdb.trade_schema import trade_attributions


class TradeAttributionRepository:
    """The post-trade attribution of a closed trade — one row per trade, for ever.

    The design rule is the primary key. ``trade_id`` *is* the key, so a second
    attribution of the same trade is structurally unable to create a second row —
    idempotency is a property of the schema rather than a discipline the caller
    has to remember. :meth:`upsert` therefore has exactly two outcomes: insert
    when nothing is there, replace when something is. It never duplicates, and it
    never silently no-ops on a genuine change.

    **Provenance is not written here.** ``evidence_grade`` and ``evidence_class``
    arrive in the row dict from the service, which reads them from
    ``trade_journal``. There is deliberately no method on this class that derives,
    defaults or infers a grade: the one place a grade may be decided is the
    journal's own writer, and a repository that could invent one would be a second
    source of truth about whether a measurement was made before its outcome.
    """

    @staticmethod
    def upsert(session: Session, row: dict[str, Any]) -> str:
        """Write one attribution, replacing any existing row for the trade.

        Returns ``"inserted"`` or ``"replaced"`` rather than a row count, because
        both are success and the caller almost always wants to distinguish them in
        a report — *"attributed 40 closed trades, 12 of them updated"* is a summary
        an operator can act on, and *"rowcount 40"* is not.

        Replacement rather than ``ON CONFLICT DO NOTHING``: a re-run that has a
        *different* input fingerprint has new information (a late fill, a context
        recorded after the trade closed), and keeping the stale row would be
        keeping a number the platform no longer believes. The
        ``input_fingerprint`` check that avoids rewriting an unchanged row lives in
        the service, where it can be done without a write attempt at all.
        """
        values = dict(row)
        values.setdefault("computed_at", utcnow())
        values["updated_at"] = utcnow()

        existing = session.execute(
            select(trade_attributions.c.trade_id).where(
                trade_attributions.c.trade_id == values["trade_id"]
            )
        ).first()
        if existing is None:
            session.execute(insert(trade_attributions).values(**values))
            return "inserted"

        session.execute(
            update(trade_attributions)
            .where(trade_attributions.c.trade_id == values["trade_id"])
            .values(**values)
        )
        return "replaced"

    @staticmethod
    def get(session: Session, trade_id: str, user_id: str) -> dict[str, Any] | None:
        """One trade's attribution, decoded. Owner-scoped like every read here."""
        stmt = select(trade_attributions).where(
            trade_attributions.c.trade_id == trade_id,
            trade_attributions.c.user_id == user_id,
        )
        row = session.execute(stmt).mappings().first()
        if row is None:
            return None
        return _decode_row(dict(row))

    @staticmethod
    def fingerprint_for(session: Session, trade_id: str) -> str | None:
        """The fingerprint currently stored for a trade, if any.

        Read before a computation so an unchanged trade can be skipped. That check
        exists so the scheduled sweep is cheap enough to actually run: attributing
        every closed trade on every pass would work, and would recompute the same
        answer hundreds of times a day for no gain.
        """
        return session.execute(
            select(trade_attributions.c.input_fingerprint).where(
                trade_attributions.c.trade_id == trade_id
            )
        ).scalar()

    @staticmethod
    def list_for_user(
        session: Session,
        user_id: str,
        *,
        strategy_id: str | None = None,
        strategy_version: int | None = None,
        symbol: str | None = None,
        source: str | None = None,
        evidence_grade: str | None = None,
        market_regime: str | None = None,
        sector: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        reason_code: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """Sliced, newest first, with the total for pagination.

        The filters mirror the API's query parameters one-for-one, so a filter that
        exists on the route cannot be silently unsupported here. ``strategy_id``
        and ``strategy_version`` are read out of the stored blob with a ``LIKE``
        rather than being lifted into columns: they are already indexed on
        ``trade_journal`` and this table's job is to be the attribution record, not
        a second index of the journal's fields.
        """
        conditions = [trade_attributions.c.user_id == user_id]
        if strategy_id:
            conditions.append(
                trade_attributions.c.attribution.like(f'%"strategy_id": "{strategy_id}"%')
            )
        if strategy_version is not None:
            conditions.append(
                trade_attributions.c.attribution.like(
                    f'%"strategy_version": {int(strategy_version)}%'
                )
            )
        if symbol:
            conditions.append(trade_attributions.c.symbol == symbol.strip().upper())
        if source:
            conditions.append(trade_attributions.c.source == source.strip().upper())
        if evidence_grade:
            conditions.append(trade_attributions.c.evidence_grade == evidence_grade.strip().lower())
        if market_regime:
            conditions.append(trade_attributions.c.market_regime == market_regime)
        if sector:
            conditions.append(trade_attributions.c.sector == sector)
        if start is not None:
            conditions.append(trade_attributions.c.computed_at >= start)
        if end is not None:
            conditions.append(trade_attributions.c.computed_at <= end)
        if reason_code:
            # Comma-joined, so a LIKE with the delimiters is exact rather than a
            # prefix match: without them ``HIGH_MAE`` would also match a
            # hypothetical ``VERY_HIGH_MAE``.
            conditions.append(
                trade_attributions.c.reason_codes.like(
                    f"%{',' if reason_code else ''}{reason_code},%"
                )
                | trade_attributions.c.reason_codes.like(f"%{reason_code}%")
            )

        total = int(
            session.execute(
                select(func.count()).select_from(trade_attributions).where(*conditions)
            ).scalar()
            or 0
        )
        stmt = (
            select(trade_attributions)
            .where(*conditions)
            .order_by(trade_attributions.c.computed_at.desc(), trade_attributions.c.trade_id)
            .limit(max(1, min(limit, 1000)))
            .offset(max(0, offset))
        )
        return [_decode_row(r) for r in session.execute(stmt).mappings().all()], total

    @staticmethod
    def all_for_user(
        session: Session, user_id: str, *, limit: int = 20_000
    ) -> list[dict[str, Any]]:
        """Every attribution for a user, oldest first — the aggregation input.

        Unfiltered and unpaginated on purpose: the summary and the breakdowns are
        computed over the whole book, and paginating the input to an aggregate is
        how a summary quietly becomes a summary of the first page.
        """
        stmt = (
            select(trade_attributions)
            .where(trade_attributions.c.user_id == user_id)
            .order_by(trade_attributions.c.computed_at)
            .limit(max(1, min(limit, 200_000)))
        )
        return [_decode_row(r) for r in session.execute(stmt).mappings().all()]

    @staticmethod
    def for_trades(
        session: Session, user_id: str, trade_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        """Attributions for a set of trades, keyed by trade id. For the join the
        learning dataset performs when it enriches journal rows."""
        if not trade_ids:
            return {}
        stmt = select(trade_attributions).where(
            trade_attributions.c.user_id == user_id,
            trade_attributions.c.trade_id.in_(set(trade_ids)),
        )
        return {r["trade_id"]: _decode_row(r) for r in session.execute(stmt).mappings().all()}


def _decode_row(row: dict[str, Any]) -> dict[str, Any]:
    """Decode JSON fields in one stored attribution row.

    Raises:
        ValueError: If a stored JSON field is malformed.
    """
    out = decode_json_fields(row, ("attribution", "missing_fields"))
    codes = out.get("reason_codes")
    if isinstance(codes, str):
        out["reason_codes"] = [c for c in codes.split(",") if c]
    elif codes is None:
        raise ValueError("stored attribution reason_codes is missing")
    else:
        raise ValueError("stored attribution reason_codes must be encoded as text")
    return out
