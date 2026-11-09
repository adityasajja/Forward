"""CLI commands for IIFL authentication, quotes, and instruments."""

from __future__ import annotations

import json
from argparse import Namespace
from typing import TYPE_CHECKING

from loguru import logger

from forward.brokers.iifl.auth import SessionStore
from forward.brokers.iifl.responses import rows as response_rows
from forward.commands.console import print_table

if TYPE_CHECKING:
    from forward.brokers.iifl.client import IiflClient


def authenticated_client() -> IiflClient | None:
    """Return an authenticated IIFL client, or log that login is needed.

    Returns:
        An authenticated broker client, or ``None`` if the session is missing.
    """
    from forward.brokers.iifl.client import IiflClient
    from forward.config.settings import get_settings

    settings = get_settings()
    client = IiflClient(
        app_key=settings.iifl_app_key,
        app_secret=settings.iifl_app_secret,
        base_url=settings.iifl_base_url,
        timeout=settings.iifl_timeout,
        session_store=SessionStore(settings.iifl_session_cache),
        force_ipv4=settings.iifl_force_ipv4,
    )
    if not client.restore_session():
        logger.error("no active session — run `forward login --print-url` and complete the login")
        return None
    return client


def run_quote(args: Namespace) -> int:
    """Fetch and print live quotes for the requested symbols.

    Args:
        args: Parsed command-line options for the quote command.

    Returns:
        The process exit code.
    """
    from forward.brokers.iifl.contracts import InstrumentMaster

    client = authenticated_client()
    if client is None:
        return 1

    symbols = [symbol.strip().upper() for symbol in args.symbols.split(",") if symbol.strip()]
    with client:
        master = InstrumentMaster(client)
        try:
            master.load_cached([args.exchange])
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "no cached instrument master for {} — run `forward instruments sync --exchanges {}`",
                args.exchange,
                args.exchange,
            )
            logger.debug("load_cached failed: {}", exc)
            return 1

        legs, resolved, failed = [], [], []
        for symbol in symbols:
            try:
                legs.append((args.exchange, master.contract_id(symbol, args.exchange)))
                resolved.append(symbol)
            except Exception as exc:  # noqa: BLE001
                failed.append((symbol, str(exc)[:120]))

        for symbol, reason in failed:
            logger.error("could not resolve {}: {}", symbol, reason)
        if not legs:
            return 1

        try:
            payload = client.market_quotes(legs)
        except Exception as exc:  # noqa: BLE001
            logger.error("quote request failed: {}", exc)
            return 1

    quotes = response_rows(payload)
    for symbol, quote in zip(resolved, quotes, strict=False):
        quote["symbol"] = symbol
    print_table(quotes, limit=len(quotes) or 1)

    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as output_file:
            json.dump(payload, output_file, indent=2, default=str)
        logger.info("wrote raw payload to {}", args.json_path)
    return 0


def run_login(args: Namespace) -> int:
    """Start IIFL login or save a session from the supplied authorization code.

    Args:
        args: Parsed command-line options for the login command.

    Returns:
        The process exit code.
    """
    from forward.brokers.iifl.auth import login_url
    from forward.config.settings import get_settings

    settings = get_settings()
    if args.print_url or not (args.client_id and args.auth_code):
        print(login_url(settings.iifl_app_key, settings.iifl_redirect_url))
        print("\nOpen the URL, log in, then re-run with --client-id and --auth-code.")
        return 0

    from forward.brokers.iifl.client import IiflClient

    client = IiflClient(
        app_key=settings.iifl_app_key,
        app_secret=settings.iifl_app_secret,
        base_url=settings.iifl_base_url,
        timeout=settings.iifl_timeout,
        session_store=SessionStore(settings.iifl_session_cache),
        force_ipv4=settings.iifl_force_ipv4,
    )
    session = client.create_session(args.client_id, args.auth_code)
    print(
        json.dumps({"client_id": session.client_id, "expires_at": session.expires_at.isoformat()})
    )
    return 0


def run_instruments(args: Namespace) -> int:
    """Search or sync the selected instrument exchanges.

    Args:
        args: Parsed command-line options for the instruments command.

    Returns:
        The process exit code.
    """
    exchanges = [exchange.strip() for exchange in args.exchanges.split(",")]
    if args.action == "search":
        from forward.wiring.instrument_catalog import get_instrument_catalog_service

        catalog = get_instrument_catalog_service()
        matches = [
            item
            for exchange in exchanges
            for item in catalog.search(args.query, exchange, args.limit)
        ][: args.limit]
        print_table(
            [
                {"symbol": item.symbol, "exchange": item.exchange, "contract_id": item.contract_id}
                for item in matches
            ],
            args.limit,
        )
        return 0

    from forward.brokers.iifl.contracts import InstrumentMaster

    client = authenticated_client()
    if client is None:
        return 1
    with client:
        master = InstrumentMaster(client)
        master.sync(exchanges)
        count = len(master.frame)
    print(f"synced {count} contracts")
    return 0
