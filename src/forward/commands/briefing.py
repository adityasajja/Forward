"""CLI command for previewing or sending the morning briefing."""

from __future__ import annotations

from argparse import Namespace


def run_briefing(args: Namespace) -> int:
    """Preview a briefing or send it through configured channels.

    Args:
        args: Parsed command-line options for the brief command.

    Returns:
        The process exit code.
    """
    from forward.alerts.channels import channels_from_settings
    from forward.composition import get_briefing_service
    from forward.config.settings import get_settings

    service = get_briefing_service()
    if args.action == "send":
        message, result = service.send(channels_from_settings(get_settings()))
        print(message)
        if result["sent_on"] == "disabled":
            print("(send disabled in config — preview only)")
            return 0
        if result["sent_on"] == "none":
            print("nothing configured to send with")
            return 1
        print(f"sent via {result['sent_on']}")
        return 0

    message, _ = service.preview()
    print(message)
    return 0
