"""Format signal scans for operators and alert channels."""

from __future__ import annotations

from datetime import datetime

from forward.core.session import IST
from forward.signals.models import ScanResult


def format_report(
    result: ScanResult, *, validated: bool = False
) -> tuple[str, str]:
    """Format a scan as a title and readable body.

    Args:
        result: Scan output to display.
        validated: Whether entry rules passed out-of-sample validation.

    Returns:
        Report title and body.
    """
    title = f"Forward signals — {datetime.now(IST):%d %b %H:%M}"
    lines: list[str] = []

    if result.sells:
        lines.append(f"SELL ({len(result.sells)})")
        for signal in result.sells:
            detail = signal.detail
            lines.append(
                f"  {signal.symbol} @ {signal.price:,.2f} | {signal.rule}: {signal.reason}"
                f" | P&L {detail.get('pnl_pct', 0):+.1f}%"
            )
        lines.append("")

    if result.buys:
        lines.append(f"BUY ({len(result.buys)})")
        for signal in result.buys:
            lines.append(
                f"  {signal.symbol} @ {signal.price:,.2f} | {signal.rule}: {signal.reason}"
            )
        if not validated:
            lines.extend(
                [
                    "",
                    "These entry rules have NOT passed out-of-sample validation. "
                    "Treat as a watchlist, not a reason to buy. Run: forward signals validate",
                ]
            )
        lines.append("")

    if result.errors:
        lines.append(f"NOT EVALUATED ({len(result.errors)})")
        lines.extend(f"  {error}" for error in result.errors[:5])
        if len(result.errors) > 5:
            lines.append(f"  ... and {len(result.errors) - 5} more")
        lines.append("")

    if not result.empty:
        lines.append("Rules are mechanical; position sizing and risk limits are yours.")

    body = "\n".join(lines).strip() or "No signals."
    return title, body
