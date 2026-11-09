"""CLI commands for checking and listing alert rules."""

from __future__ import annotations

from argparse import Namespace


def run_alerts(args: Namespace) -> int:
    """List alert rules, send a test, or evaluate current market quotes.

    Args:
        args: Parsed command-line options for the alerts command.

    Returns:
        The process exit code.
    """
    from forward.brokers.iifl.alert_quotes import IiflAlertQuoteAdapter
    from forward.core.errors import BrokerUnavailable
    from forward.wiring.alerts import get_alert_service
    from forward.wiring.broker_access import get_authenticated_broker_client

    service = get_alert_service()
    if args.action == "list":
        for rule in service.rules():
            state = "ARMED" if rule.armed else "off"
            print(f"[{state}] {rule.id} {rule.display} (cooldown {rule.cooldown_min}m)")
        return 0
    if args.action == "test":
        channel = service.send_test()
        if channel != "none":
            print(f"test message sent via {channel}")
            return 0
        print("nothing configured — message went nowhere; set TELEGRAM_BOT_TOKEN/CHAT_ID")
        return 1

    try:
        client = get_authenticated_broker_client()
    except BrokerUnavailable as exc:
        print(str(exc))
        return 1
    with client:
        market_open, fired = service.check(IiflAlertQuoteAdapter(client))
    print(f"market_open={market_open} fired={len(fired)}")
    for event in fired:
        print(f"[{event.channel}] {event.rule} — {event.message}")
    return 0
