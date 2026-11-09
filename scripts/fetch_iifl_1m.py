"""Fetch 1-minute candles from IIFL for the NIFTY 50 + Midcap 150 universe.

Uses the cached JWT session (no browser re-auth needed if still valid).
Writes parquet files to data/iifl_1m/<SYMBOL>.parquet.
"""
import argparse
import json
import time
import traceback
from pathlib import Path

import pandas as pd

from forward.brokers.iifl.auth import SessionStore
from forward.brokers.iifl.client import IiflClient
from forward.brokers.iifl.feeds import candles_to_frame

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "fetch_iifl_minute_history.json"

# ------------------------------------------------------------------ helpers
def load_universe(config: dict) -> list[str]:
    """Load the ordered, unique symbols listed in the fetch configuration.

    Args:
        config: Explicit data-fetch configuration.

    Returns:
        Symbols in their first-seen order.
    """
    syms: list[str] = []
    for relative_path in config["universe_files"]:
        content = (ROOT / relative_path).read_text(encoding="utf-8").strip()
        parts = [s.strip() for s in content.replace("\n", ",").split(",") if s.strip()]
        syms.extend(parts)
    return list(dict.fromkeys(syms))  # de-dup, preserve order


def load_contracts(config: dict) -> dict[str, str]:
    """Map configured underlying symbols to broker instrument IDs.

    Args:
        config: Explicit data-fetch configuration.

    Returns:
        A map from normalized symbol to instrument ID.
    """
    rows = json.loads((ROOT / config["contracts_file"]).read_text(encoding="utf-8"))
    m: dict[str, str] = {}
    for r in rows:
        symbol = r["underlyingInstrumentSymbol"]
        instrument_id = r["instrumentId"]
        for suffix in config["symbol_suffixes"]:
            symbol = symbol.removesuffix(suffix)
        m[symbol] = instrument_id
    return m


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    config_path = parser.parse_args().config
    config = json.loads(config_path.read_text(encoding="utf-8"))
    print(f"Configuration: {config_path.resolve()}")
    output_directory = ROOT / config["output_directory"]
    output_directory.mkdir(parents=True, exist_ok=True)
    universe = load_universe(config)
    contracts = load_contracts(config)
    total = len(universe)

    # Build symbol → instrument_id
    pairs = [(s, contracts[s]) for s in universe if s in contracts]
    missing = [s for s in universe if s not in contracts]
    print(f"Universe: {total} symbols, {len(pairs)} have instrument IDs, "
          f"{len(missing)} missing contracts")
    if missing:
        print(
            "  Missing: "
            f"{missing[:config['missing_contract_display_limit']]}"
        )

    # Restore session
    store = SessionStore(str(ROOT / config["session_file"]))
    sess = store.load()
    if sess is None:
        print("ERROR: No cached IIFL session found. Browser OAuth required.")
        return 1

    client = IiflClient(session_store=store)
    client.set_session(sess)
    print(f"Session restored for client_id={sess.client_id}")
    print(f"Starting 1-minute fetch for {len(pairs)} symbols "
          f"({config['from_date']} → {config['to_date']})")

    fetched = 0
    errors = 0
    for i, (symbol, iid) in enumerate(pairs, 1):
        out_path = output_directory / f"{symbol}.parquet"
        # Skip if we already have a reasonably full file
        if out_path.exists():
            df = pd.read_parquet(out_path)
            if len(df) > config["minimum_cached_bars"]:
                print(f"  [{i}/{total}] {symbol}: skip ({len(df)} bars cached)")
                continue

        try:
            payload = client.historical_data(
                exchange=config["exchange"],
                instrument_id=iid,
                interval=config["interval"],
                from_date=config["from_date"],
                to_date=config["to_date"],
            )
            df = candles_to_frame(payload)
            if df.empty:
                print(f"  [{i}/{total}] {symbol}: NO DATA (NOT FOUND)")
                errors += 1
                continue

            df.to_parquet(out_path, index=False)
            fetched += 1
            print(f"  [{i}/{total}] {symbol}: {len(df)} bars "
                  f"({df['ts'].iloc[0].date()} → {df['ts'].iloc[-1].date()})")

            # Rate limiting — IIFL API is sensitive
            time.sleep(config["request_pause_seconds"])

        except Exception as e:
            errors += 1
            print(f"  [{i}/{total}] {symbol}: ERROR — {e}")
            traceback.print_exc(limit=config["error_traceback_limit"])
            time.sleep(config["request_pause_seconds"])

    print(f"\n{'='*60}")
    print(f"Fetch complete: {fetched} symbols fetched, {errors} errors")
    print(f"Data saved to {output_directory}")

    # Count total bars
    total_bars = 0
    n_files = 0
    for f in sorted(output_directory.glob("*.parquet")):
        df = pd.read_parquet(f)
        total_bars += len(df)
        n_files += 1
    print(f"Total: {n_files} files, ~{total_bars:,} bars")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
