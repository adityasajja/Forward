"""CLI command for market-history maintenance and scans."""

from __future__ import annotations

from argparse import Namespace
from datetime import datetime


def run_history(args: Namespace) -> int:
    """Run one market-history maintenance or scan action.

    Args:
        args: Parsed command-line options for the history command.

    Returns:
        The process exit code.
    """
    import pandas as pd

    if args.action == "refresh-eod":
        # No broker session needed: tops up the tracked names from public daily bars.
        from forward.config.settings import research_data_root
        from forward.data.eod_refresh import refresh
        from forward.wiring.market_intel import get_market_intel_service

        print(refresh(research_data_root(), get_market_intel_service().get_universe_symbols()))
        return 0

    if args.action == "sync":
        from forward.brokers.iifl.history_sync import IiflHistorySyncAdapter
        from forward.wiring.broker_access import get_authenticated_broker_client

        IiflHistorySyncAdapter(get_authenticated_broker_client).sync_all(
            args.exchange,
            args.interval,
            args.from_date,
            args.to_date,
            workers=args.workers,
            start=args.start,
            end=args.end,
        )
        return 0

    if args.action == "sync-index":
        if not all((args.index_name, args.index_contract_source, args.index_interval)):
            raise SystemExit(
                "history sync-index requires --index-name, "
                "--index-contract-source, and --index-interval"
            )
        from forward.config.settings import get_settings, research_data_root
        from forward.data.indices import sync_index
        from forward.wiring.broker_access import get_authenticated_broker_client

        result = sync_index(
            get_authenticated_broker_client(),
            research_data_root(),
            name=args.index_name,
            base_url=get_settings().iifl_base_url,
            contract_source=args.index_contract_source,
            interval=args.index_interval,
        )
        print(f"{args.index_name} index: {result}")
        return 0

    from forward.config.settings import research_data_root
    from forward.wiring.research import get_local_scan_service

    result = get_local_scan_service().scan_all(args.exchange)
    scan = pd.DataFrame(result["rows"])
    market_day = result["as_of"] or datetime.now().date().isoformat()
    out = research_data_root() / "scans" / f"scan_all_{market_day.replace('-', '')}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    scan.to_csv(out, index=False)
    up = result["breadth_up"]
    print(f"scored {result['scored']}/{result['universe']} names -> {out}")
    print(f"breadth: {up} UP / {result['scored'] - up} not-UP")
    print("\n--- top 10 ---\n" + scan.head(10).to_string(index=False))
    print("\n--- bottom 10 ---\n" + scan.tail(10).to_string(index=False))
    breakout = scan[scan["breakout"]]
    if not breakout.empty:
        print(
            "\n--- breakouts ---\n"
            + breakout[["symbol", "last", "ret_1m", "vol_x"]].to_string(index=False)
        )
    return 0
