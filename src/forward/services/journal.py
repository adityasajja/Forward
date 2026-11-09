"""Trade journaling — turning a fill log into trade *episodes*.

Why this exists
---------------

The order log records fills. A fill log answers "what did it buy and sell"; it
does not answer "how long was the trade held", "how far did it run before it came
back", and "what was the worst drawdown inside the trade". Those are episode
properties, and ``trade_journal`` is the table that holds them.

The journal has had no writer. That is why it is empty, and this module is the
writer.

The design rule
---------------

**The journal is a projection of the position fold, not a second copy of it.**

``PaperLedger`` already folds ``order_events`` into positions, and the account's
P&L comes from that fold. If the journal tracked trades independently, the two
could disagree — and a journal that disagrees with the account is worse than no
journal, because it looks like evidence. So an entry is opened when the fold
shows a position going from flat to non-flat, and closed when it returns to flat.
The journal is therefore always a consequence of the same arithmetic the P&L is.

Reconciliation rather than event hooks
--------------------------------------

``reconcile`` is idempotent and can be called at any time, because it compares the
journal to the fold instead of reacting to individual fills. That matters for
three reasons:

* a fill recorded during a restart is not lost, because the next reconcile sees it;
* it cannot double-open a trade, because it checks for an existing open episode;
* it is safe to run on a schedule, so the journal repairs itself.

This is deliberately not hooked into ``record_fill``. Journaling is a reporting
concern, and the OMS should not have to know that reports exist.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from forward.core.enums import Side
from forward.core.trade_journal_ports import (
    TradeJournalStoragePort,
    TradeJournalUnitOfWorkPort,
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _naive(ts: Any) -> datetime | None:
    """A timestamp as naive UTC, so a stored value and a fill's can be compared."""
    if ts is None:
        return None
    if getattr(ts, "tzinfo", None) is not None:
        return ts.astimezone(UTC).replace(tzinfo=None)
    return ts


class TradeJournalService:
    """Opens and closes journal episodes from the deployment's folded positions."""

    def __init__(self, *, storage: TradeJournalStoragePort, ledger: Any) -> None:
        """Create the journal use case with its storage and paper ledger.

        Args:
            storage: Port used to read and write journal records.
            ledger: Paper ledger that folds recorded fills into positions.
        """
        self._storage = storage
        self._ledger = ledger

    # ------------------------------------------------------------------
    def reconcile(self, user_id: str, deployment_id: str) -> dict[str, Any]:
        """Bring the journal into agreement with the folded book.

        Returns a small report — ``opened`` and ``closed`` — rather than nothing,
        so a scheduled caller can log what it did and a silent no-op is
        distinguishable from a silent failure.
        """
        portfolio, fills = self._ledger.portfolio_and_fills(
            user_id, deployment_id=deployment_id
        )

        opened: list[str] = []
        closed: list[str] = []

        with self._storage.transaction() as journal:
            existing = journal.entries(user_id, deployment_id)
            open_by_symbol = {
                row["symbol"]: row for row in existing if row.get("exit_ts") is None
            }
            known_symbols = {row["symbol"] for row in existing}

            for symbol, position in portfolio.positions.items():
                quantity = float(position.quantity)
                if abs(quantity) < 1e-9:
                    # Flat. If an episode is open for this symbol it has ended,
                    # so close it against the last fill.
                    episode = open_by_symbol.get(symbol)
                    if episode is not None:
                        episode_fills = self._fills_for_episode(episode, fills)
                        trade_id = self._close(journal, episode, episode_fills, user_id)
                        if trade_id:
                            closed.append(trade_id)
                    continue

                if symbol in open_by_symbol:
                    continue

                # A position exists with no open episode. Either it is new, or an
                # episode was closed and then the position was re-opened. The
                # symbol having been journalled before does not make this the same
                # trade — the fold says flat-then-not-flat, and that is a new
                # episode.
                entry = self._entry_fill(fills, symbol)
                if entry is None:
                    continue
                # Strategy attribution comes from the fill's order, not from the
                # instrument: an instrument is a market object and has no idea
                # which strategy traded it. The same read also establishes the
                # provenance grade, which is why it is taken at open rather than
                # at close — the entry order is the one whose creation time
                # decides whether this trade was raised live.
                attribution = self._attribution(journal, entry["order_id"])
                row = journal.open_entry(
                    user_id=user_id,
                    symbol=symbol,
                    side="BUY" if quantity > 0 else "SELL",
                    quantity=abs(quantity),
                    entry_price=float(entry["price"]),
                    entry_ts=entry["ts"],
                    deployment_id=deployment_id,
                    strategy_id=attribution.get("strategy_id"),
                    strategy_version=attribution.get("strategy_version"),
                    signal_reason=attribution.get("reason"),
                    evidence_grade=attribution.get("evidence_grade"),
                )
                opened.append(row["trade_id"])

            # A symbol that left the book entirely is closed too — its position
            # was dropped rather than flattened, which happens when a deployment
            # is reset.
            for symbol, episode in open_by_symbol.items():
                if symbol in portfolio.positions:
                    continue
                episode_fills = self._fills_for_episode(episode, fills)
                trade_id = self._close(journal, episode, episode_fills, user_id)
                if trade_id:
                    closed.append(trade_id)

            stale = [
                row["trade_id"]
                for row in existing
                if row.get("exit_ts") is None and row["symbol"] not in portfolio.positions
                and row["trade_id"] not in closed
            ]
            if stale:
                logger.debug(
                    "journal: %d open episodes for symbols no longer held (%s)",
                    len(stale),
                    ", ".join(sorted(known_symbols))[:120],
                )

            # A round trip that began and ended between two reconciles was never seen as an open
            # position, so the pass above could not journal it. The fills still hold it, so every
            # closed flat-to-flat episode in them is journalled here if it is not already.
            journalled = [
                (row["symbol"], _naive(row.get("entry_ts")), _naive(row.get("exit_ts")))
                for row in existing
            ]
            for symbol, first, last, episode_fills in self._closed_episodes(fills):
                start, end = _naive(first.ts), _naive(last.ts)
                if any(
                    s == symbol and e_in is not None and start is not None and end is not None
                    and start <= e_in <= end
                    for s, e_in, _ in journalled
                ):
                    continue
                attribution = self._attribution(
                    journal, getattr(first, "order_id", None)
                )
                row = journal.open_entry(
                    user_id=user_id,
                    symbol=symbol,
                    side="BUY" if first.side.sign > 0 else "SELL",
                    quantity=float(first.quantity),
                    entry_price=float(first.price),
                    entry_ts=first.ts,
                    deployment_id=deployment_id,
                    strategy_id=attribution.get("strategy_id"),
                    strategy_version=attribution.get("strategy_version"),
                    signal_reason=attribution.get("reason"),
                    evidence_grade=attribution.get("evidence_grade"),
                )
                trade_id = self._close(journal, row, episode_fills, user_id)
                if trade_id:
                    opened.append(row["trade_id"])
                    closed.append(trade_id)
                    journalled.append((symbol, start, end))

        return {"opened": opened, "closed": closed, "open_now": len(open_by_symbol)}

    @staticmethod
    def _closed_episodes(fills: list[Any]) -> list[tuple[str, Any, Any, list[Any]]]:
        """Every flat-to-flat round trip in a fill sequence: ``(symbol, first, last, fills)``.

        The same definition the position fold uses: an episode opens when a symbol leaves flat and
        closes when it returns to flat. An episode still open at the end is not returned.
        """
        book: dict[str, list[Any]] = {}
        qty: dict[str, float] = {}
        out: list[tuple[str, Any, Any, list[Any]]] = []
        for fill in sorted(fills, key=lambda f: f.ts):
            symbol = getattr(fill.instrument, "symbol", None)
            if symbol is None:
                continue
            was_flat = abs(qty.get(symbol, 0.0)) < 1e-9
            qty[symbol] = qty.get(symbol, 0.0) + fill.side.sign * float(fill.quantity)
            book[symbol] = [fill] if was_flat else [*book.get(symbol, []), fill]
            if abs(qty[symbol]) < 1e-9 and not was_flat:
                episode = book.pop(symbol)
                out.append((symbol, episode[0], fill, episode))
        return out

    @staticmethod
    def _fills_for_episode(episode: dict[str, Any], fills: list[Any]) -> list[Any]:
        """Find the closed fill sequence that contains a journal entry.

        Args:
            episode: Existing journal row to close.
            fills: All fills returned by the ledger for this deployment.

        Returns:
            Fills from that flat-to-flat episode, or an empty list if none exists.
        """
        entry_ts = _naive(episode.get("entry_ts"))
        if entry_ts is None:
            return []

        matches = [
            (first_ts, episode_fills)
            for symbol, first, last, episode_fills in TradeJournalService._closed_episodes(fills)
            if symbol == episode["symbol"]
            and (first_ts := _naive(first.ts)) is not None
            and (last_ts := _naive(last.ts)) is not None
            and first_ts <= entry_ts <= last_ts
        ]
        if not matches:
            return []
        return max(matches, key=lambda match: match[0])[1]

    # ------------------------------------------------------------------
    @staticmethod
    def _entry_fill(fills: list[Any], symbol: str) -> dict[str, Any] | None:
        """The most recent fill for a symbol — the one that opened the position.

        The *latest* rather than the first: a symbol that was traded, flattened
        and traded again has several fills, and the episode being opened now
        belongs to the most recent one.
        """
        matching = [f for f in fills if getattr(f.instrument, "symbol", None) == symbol]
        if not matching:
            return None
        latest = matching[-1]
        price = float(latest.price)
        if not math.isfinite(price) or price <= 0:
            raise ValueError(f"entry fill for {symbol} must have a finite positive price")
        return {
            "price": price,
            "ts": getattr(latest, "ts", None),
            "order_id": getattr(latest, "order_id", None),
        }

    @staticmethod
    def _attribution(
        journal: TradeJournalUnitOfWorkPort, order_id: str | None
    ) -> dict[str, Any]:
        """Which strategy version raised the order, why, and on what evidence.

        The reason was written to the order's ``NEW`` event, so it lives in the
        event log rather than on the order row. A missing order is not an error:
        an episode can still be journalled without attribution, and refusing to
        record it would lose the P&L to protect a label.

        ``evidence_grade`` is the provenance verdict, and it is read from the
        same event. The OMS stamps every order it raises with
        :data:`forward.execution.oms.PROVENANCE_FORWARD` at the instant of creation;
        an order carrying that stamp was raised live, so the trade it opened is
        forward evidence. An order without it was inserted into the table by
        something other than the order-management system — a backfill harness, a
        migration, a test — and the record therefore **cannot demonstrate it was
        written before its own outcome**. That case grades ``in_sample``.

        The direction matters. Grading an unstamped order forward would let a
        replay of last year's prices be counted as out-of-sample confirmation of
        the rule that produced them, which is the one error the whole evidence
        vocabulary exists to prevent. Grading it in-sample merely understates
        how much has been proven.
        """
        if not order_id:
            return {}
        from forward.core.learning_evidence import GRADE_FORWARD, GRADE_IN_SAMPLE
        from forward.execution.oms import PROVENANCE_FORWARD, PROVENANCE_KEY

        found, events = journal.order_attribution_data(order_id)
        if found is None:
            return {}

        reason = None
        stamped = False
        for event in events:
            if event.get("to_status") != "NEW":
                continue
            raw = event.get("raw")
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except (ValueError, TypeError):
                    raw = None
            if not isinstance(raw, dict):
                continue
            if reason is None and raw.get("reason"):
                reason = raw["reason"]
            if raw.get(PROVENANCE_KEY) == PROVENANCE_FORWARD:
                stamped = True
        return {
            "strategy_id": found["strategy_id"],
            "strategy_version": found["strategy_version"],
            "reason": reason,
            "evidence_grade": GRADE_FORWARD if stamped else GRADE_IN_SAMPLE,
        }

    @staticmethod
    def _close(
        journal: TradeJournalUnitOfWorkPort,
        episode: dict[str, Any],
        fills: list[Any],
        user_id: str,
    ) -> str | None:
        """Close one episode at the last fill for its symbol, with MFE and MAE.

        The excursion figures come from the fills between the episode's entry and
        now — which is what the log actually knows. They are *realised* excursions,
        not intrabar: a price that spiked between two fills is not in the log, and
        reporting it would mean inventing data.

        Slippage and the exit reason are recorded here too, and both are
        measurements rather than inferences:

        * **Slippage** is already on each fill, in rupees per unit and adverse
          positive, derived by the ledger from the order's ``requested_price``
          and the price it actually got. The journal stores one figure per trade,
          so it stores the **mean of the legs that could be measured** — one
          number per leg, averaged over the legs that have one. A leg with no
          recorded reference price is not a leg with no slippage, so it is
          excluded rather than counted as zero, and a round trip where neither
          leg could be measured records ``None``.
        * **The exit reason** is the closing order's own reason, read from its
          ``NEW`` event exactly as the entry's is. It is a property of the
          decision, not of the price path, so it cannot be recovered from the
          fills: it is recorded or it is absent.
        """
        symbol = episode["symbol"]
        entries = [
            f
            for f in fills
            if getattr(f.instrument, "symbol", None) == symbol
            and getattr(f, "ts", None) is not None
            and episode["entry_ts"] is not None
            and f.ts >= episode["entry_ts"]
        ]
        if not entries:
            return None

        exit_fill = entries[-1]
        entry_price = float(episode["entry_price"])
        exit_price = float(exit_fill.price)
        quantity = float(episode["quantity"])
        side = Side.parse(episode.get("side"))
        if not math.isfinite(entry_price) or entry_price <= 0:
            raise ValueError("journal entry price must be finite and positive")
        if not math.isfinite(exit_price) or exit_price <= 0:
            raise ValueError("journal exit price must be finite and positive")
        if not math.isfinite(quantity) or quantity <= 0:
            raise ValueError("journal quantity must be finite and positive")
        sign = float(side.sign)

        gross = (exit_price - entry_price) * quantity * sign
        # MFE and MAE are the best and worst favour the position saw, measured on
        # the prices the log holds.
        prices = [float(f.price) for f in entries]
        if any(not math.isfinite(price) or price <= 0 for price in prices):
            raise ValueError("journal fill prices must be finite and positive")
        best = max(prices) if sign > 0 else min(prices)
        worst = min(prices) if sign > 0 else max(prices)
        mfe = (best - entry_price) * quantity * sign
        mae = (worst - entry_price) * quantity * sign

        commissions = [getattr(fill, "commission", None) for fill in entries]
        commission = (
            sum(abs(float(value)) for value in commissions)
            if commissions and all(value is not None for value in commissions)
            else None
        )

        # The legs of the round trip: the fill that opened the episode and the
        # one that closed it. An episode with several fills between them is
        # measured on its two ends, which is what "round trip" means.
        entry_fill = entries[0]
        leg_bps = [
            value
            for value in (
                _fill_slippage_bps(entry_fill),
                _fill_slippage_bps(exit_fill),
            )
            if value is not None
        ]
        slippage_bps = sum(leg_bps) / len(leg_bps) if leg_bps else None

        closing = TradeJournalService._attribution(
            journal, getattr(exit_fill, "order_id", None)
        )
        exit_reason = closing.get("reason")

        updated = journal.close_entry(
            episode["trade_id"],
            user_id,
            exit_price=exit_price,
            gross_pnl=gross,
            net_pnl=gross - commission if commission is not None else None,
            exit_ts=getattr(exit_fill, "ts", None) or _utcnow(),
            mfe=mfe,
            mae=mae,
            slippage_bps=slippage_bps,
            exit_reason=exit_reason,
        )
        return episode["trade_id"] if updated else None


def _fill_slippage_bps(fill: Any) -> float | None:
    """One fill's slippage in basis points, adverse positive.

    ``Fill.slippage`` is rupees **per unit** and is already sign-corrected by the
    ledger — it derives it from the order's ``requested_price`` against the price
    the fill actually got — so this only rescales it against that price.

    ``None`` when either figure is missing. A fill whose reference price was
    never recorded had *no measurable* slippage, which is not the same statement
    as zero slippage, and averaging a zero in would drag the round trip's figure
    toward a friction that was never measured.
    """
    price = float(getattr(fill, "price", 0.0) or 0.0)
    slippage = getattr(fill, "slippage", None)
    if not price or slippage is None:
        return None
    return float(slippage) / price * 10_000.0
