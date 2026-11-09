"""Fetch configured additional IIFL minute history."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from forward.brokers.iifl.auth import SessionStore
from forward.brokers.iifl.client import IiflClient, _fmt_date
from forward.brokers.iifl.contracts import InstrumentMaster
from forward.brokers.iifl.feeds import candles_to_frame
from forward.config.settings import get_settings

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "fetch_iifl_additional_symbols.json"


def main() -> int:
    """Fetch the symbols and date range defined by the supplied config."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    config_path = parser.parse_args().config
    config = json.loads(config_path.read_text(encoding="utf-8"))
    print(f"Configuration: {config_path.resolve()}", flush=True)
    settings = get_settings()
    store = SessionStore(settings.iifl_session_cache)
    session = store.load()
    if session is None:
        print("No cached IIFL session. Run `forward login` first.", flush=True)
        return 1

    client = IiflClient(
        app_key=settings.iifl_app_key,
        app_secret=settings.iifl_app_secret,
        session_store=store,
    )
    client.set_session(session)
    profile = client.profile()
    print(f"Session: {session.client_id}, expires {session.expires_at}", flush=True)
    print(f"Profile: {profile['result']['clientId']}", flush=True)

    exchange = config["exchange"]
    master = InstrumentMaster(client)
    master.sync([exchange])

    cache_directory = ROOT / config["cache_directory"]
    cache_directory.mkdir(parents=True, exist_ok=True)
    from_date = datetime.fromisoformat(config["from_date"])
    to_date = datetime.fromisoformat(config["to_date"])
    fetched: list[str] = []
    for symbol in config["symbols"]:
        cache_path = cache_directory / f"{symbol}.parquet"
        if config["skip_existing_cache"] and cache_path.exists():
            frame = pd.read_parquet(cache_path)
            print(f"  {symbol}: already cached ({len(frame)} bars)", flush=True)
            fetched.append(symbol)
            continue

        instruments = master.search(symbol, exchange=exchange)
        if instruments.empty:
            print(f"  {symbol}: not found in instrument master", flush=True)
            continue
        instrument_id = str(instruments.iloc[0]["conid"])

        chunks: list[pd.DataFrame] = []
        cursor = from_date
        chunk_count = 0
        while cursor < to_date:
            chunk_end = min(
                cursor + timedelta(days=config["chunk_days"]),
                to_date,
            )
            payload = client.historical_data(
                exchange,
                instrument_id,
                config["interval"],
                _fmt_date(cursor),
                _fmt_date(chunk_end),
            )
            frame = candles_to_frame(payload)
            if not frame.empty:
                frame["symbol"] = symbol
                chunks.append(frame)
            chunk_count += 1
            cursor = chunk_end + timedelta(days=1)
            time.sleep(config["pause_between_chunks_seconds"])

        if not chunks:
            print(f"  {symbol}: no data", flush=True)
            continue

        frame = pd.concat(chunks, ignore_index=True).sort_values("ts").reset_index(drop=True)
        frame.to_parquet(cache_path, index=False)
        fetched.append(symbol)
        print(f"  {symbol}: {len(frame)} bars ({chunk_count} chunks)", flush=True)
        time.sleep(config["pause_between_symbols_seconds"])

    print(f"\nFetched or cached: {fetched}", flush=True)
    print(
        f"Total cached stocks: {len(list(cache_directory.glob('*.parquet')))}",
        flush=True,
    )
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
