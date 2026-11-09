"""Format trade signal messages for alert channels."""

from __future__ import annotations

from forward.core.trade_signal_models import TradeSignal


def format_telegram_preview(signal: TradeSignal) -> tuple[str, str]:
    """Format a pending signal for Telegram preview.

    Args:
        signal: Pending signal shown to the account owner.

    Returns:
        The Telegram message header and body.
    """
    icon = "🟢" if signal.action == "BUY" else "🔴"
    symbol = signal.symbol.replace("-EQ", "")
    header = f"{icon} {signal.action} Signal — {symbol}"
    body = (
        f"Setup: {signal.setup}\n"
        f"Reason: {signal.reason}\n\n"
        f"• Entry:  ₹{signal.entry_price:,.2f}\n"
        f"• Stop:   ₹{signal.stop_loss:,.2f}\n"
        f"• Target: ₹{signal.target:,.2f}\n"
        f"• Qty:    {signal.quantity} shares\n"
        f"• Risk:   ₹{signal.risk_amount:,.0f} ({signal.rr_ratio:.1f}:1 R:R)"
    )
    if signal.rsi is not None:
        body += f"\n• RSI:    {signal.rsi:.0f}"
    body += "\n\nOpen dashboard → Scanner → Trade Signals to execute."
    return header, body


def format_telegram_confirmation(signal: TradeSignal) -> tuple[str, str]:
    """Format the successful execution notice for Telegram.

    Args:
        signal: Signal whose bracket orders were placed.

    Returns:
        The Telegram message header and body.
    """
    symbol = signal.symbol.replace("-EQ", "")
    header = f"✅ Order Placed — {symbol}"
    body = (
        f"Action: {signal.action} {signal.quantity} shares\n"
        f"Entry:  ₹{signal.entry_price:,.2f}\n"
        f"SL:     ₹{signal.stop_loss:,.2f}\n"
        f"Target: ₹{signal.target:,.2f}\n"
        f"Risk:   ₹{signal.risk_amount:,.0f}\n"
        "Orders tagged: FORWARD-SEMI"
    )
    return header, body
