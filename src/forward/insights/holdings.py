"""Your holdings, from the broker when a session exists and from the last snapshot otherwise.

The alert engine used to call the broker for holdings and swallow the failure, so with an
expired session it quietly watched nothing. Here a successful read is saved, and the last
saved copy is used when there is no session, with its age reported so the page can say so.
"""

from __future__ import annotations

from typing import Any


def normalize(rows: Any) -> list[dict[str, Any]]:
    """Broker rows -> ``{symbol, qty, avg_price}``, tolerant of IIFL's field names."""
    if isinstance(rows, dict):
        rows = rows.get("result", [])
    out: list[dict[str, Any]] = []
    for h in rows or []:
        raw = str(
            h.get("symbol") or h.get("TradingSymbol") or h.get("tradingSymbol") or h.get("nseTradingSymbol") or ""
        ).strip()
        if not raw:
            continue
        symbol = raw if raw.endswith("-EQ") else f"{raw}-EQ"
        qty = int(float(h.get("qty") or h.get("TotalQty") or h.get("quantity") or h.get("totalQuantity") or 0))
        avg = float(
            h.get("avg_price") or h.get("BuyAvgRate") or h.get("averagePrice") or h.get("averageTradedPrice") or 0
        )
        if qty > 0:
            out.append({"symbol": symbol, "qty": qty, "avg_price": avg})
    return _one_row_per_stock(out)


def _one_row_per_stock(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge a stock the broker lists in more than one lot into a single holding.

    The same share can appear twice (delivery and collateral, or two settlement lots).
    Anything that keys a holding by symbol would otherwise keep only one of them and
    silently drop the other's shares.
    """
    merged: dict[str, dict[str, Any]] = {}
    for r in rows:
        cur = merged.get(r["symbol"])
        if cur is None:
            merged[r["symbol"]] = dict(r)
            continue
        total = cur["qty"] + r["qty"]
        cur["avg_price"] = round((cur["avg_price"] * cur["qty"] + r["avg_price"] * r["qty"]) / total, 4)
        cur["qty"] = total
    return list(merged.values())
