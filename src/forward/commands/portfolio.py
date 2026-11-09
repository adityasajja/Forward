"""CLI command for reading the broker portfolio."""

from __future__ import annotations

import json
from argparse import Namespace

from loguru import logger

from forward.commands.broker import authenticated_client
from forward.commands.console import print_table


def run_portfolio(args: Namespace) -> int:
    """Show broker account sections, with optional JSON and CSV output.

    Args:
        args: Parsed command-line options for the portfolio command.

    Returns:
        One when any broker section fails; otherwise zero.
    """
    from forward.wiring.broker_data import build_broker_account_read_service

    client = authenticated_client()
    if client is None:
        return 1

    service = build_broker_account_read_service(client)
    with client:
        raw = service.sections(None if args.section == "all" else args.section)
    sections = list(raw)
    failed = False

    for name in sections:
        section = raw[name]
        rows = section["rows"]
        print(f"\n=== {name} ===")
        if section.get("error"):
            failed = True
            logger.error("{} failed: {}", name, section["error"])
            continue
        if not rows:
            print("  (empty)")
            continue
        if name == "limits":
            for key, value in rows[0].items():
                print(f"  {str(key):32} {value}")
        else:
            print_table(rows, args.limit)

    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as fh:
            json.dump(raw, fh, indent=2, default=str)
        logger.info("wrote raw payload to {}", args.json_path)

    if args.save_dir:
        import os

        import pandas as pd

        os.makedirs(args.save_dir, exist_ok=True)
        written = 0
        for name in sections:
            rows = raw[name]["rows"]
            if not rows:
                continue
            path = os.path.join(args.save_dir, f"{name}.csv")
            pd.DataFrame(rows).to_csv(path, index=False)
            written += 1
            logger.info("wrote {}", path)
        if not written:
            logger.warning("nothing to save — every section came back empty or errored")
    return 1 if failed else 0
