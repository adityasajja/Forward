"""Fetch configured IIFL minute history with a shared broker client."""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
from fetch_iifl_1m import load_contracts, load_universe

from forward.brokers.iifl.auth import SessionStore
from forward.brokers.iifl.client import IiflClient
from forward.brokers.iifl.feeds import candles_to_frame

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "fetch_iifl_minute_history.json"


def fetch_one(
    symbol: str,
    instrument_id: str,
    client: IiflClient,
    config: dict,
    output_directory: Path,
) -> tuple[str, str]:
    """Fetch and save a symbol's minute bars.

    Args:
        symbol: Canonical symbol used for the cache filename.
        instrument_id: Broker instrument identifier.
        client: Authenticated IIFL client.
        config: Explicit history-fetch settings.
        output_directory: Directory that receives parquet files.

    Returns:
        The symbol and a status label with its bar count when fetched.
    """
    out_path = output_directory / f"{symbol}.parquet"
    if out_path.exists():
        cached = pd.read_parquet(out_path)
        if len(cached) > config["minimum_cached_bars"]:
            return symbol, f"cached:{len(cached)}"

    payload = client.historical_data(
        exchange=config["exchange"],
        instrument_id=instrument_id,
        interval=config["interval"],
        from_date=config["from_date"],
        to_date=config["to_date"],
    )
    frame = candles_to_frame(payload)
    if frame.empty:
        return symbol, "empty"
    frame.to_parquet(out_path, index=False)
    return symbol, f"fetched:{len(frame)}"


def main() -> int:
    """Run the configured parallel history download."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    config_path = parser.parse_args().config
    config = json.loads(config_path.read_text(encoding="utf-8"))
    print(f"Configuration: {config_path.resolve()}")
    output_directory = ROOT / config["output_directory"]
    output_directory.mkdir(parents=True, exist_ok=True)

    universe = load_universe(config)
    contracts = load_contracts(config)
    pairs = [(symbol, contracts[symbol]) for symbol in universe if symbol in contracts]
    print(f"Universe: {len(universe)} symbols, {len(pairs)} have instrument IDs")

    store = SessionStore(str(ROOT / config["session_file"]))
    session = store.load()
    if session is None:
        print("ERROR: No cached IIFL session")
        return 1

    client = IiflClient(session_store=store)
    client.set_session(session)
    print(f"Session OK (client_id={session.client_id})")

    fetched = 0
    errors = 0
    with ThreadPoolExecutor(max_workers=config["max_workers"]) as pool:
        futures = {
            pool.submit(fetch_one, symbol, instrument_id, client, config, output_directory): symbol
            for symbol, instrument_id in pairs
        }
        for number, future in enumerate(
            as_completed(futures, timeout=config["parallel_timeout_seconds"]),
            start=1,
        ):
            symbol = futures[future]
            try:
                _, status = future.result()
                print(f"  [{number}/{len(pairs)}] {symbol}: {status}", flush=True)
                if status.startswith(("fetched:", "cached:")):
                    fetched += 1
                else:
                    errors += 1
            except Exception as exc:
                print(f"  [{number}/{len(pairs)}] {symbol}: ERROR — {exc}", flush=True)
                errors += 1

    parquet_files = list(output_directory.glob("*.parquet"))
    total_bars = sum(len(pd.read_parquet(path)) for path in parquet_files)
    print(f"\nDone: {fetched} fetched, {errors} errors")
    print(f"Total cached: {len(parquet_files)} files, {total_bars:,} bars")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
