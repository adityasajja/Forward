"""Fetch the configured NIFTYBEES minute-history interval from IIFL."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from forward.brokers.iifl.auth import SessionStore
from forward.brokers.iifl.client import IiflClient, _fmt_date
from forward.brokers.iifl.contracts import InstrumentMaster
from forward.brokers.iifl.feeds import candles_to_frame
from forward.config.settings import get_settings

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "fetch_iifl_niftybees_history.json"


def main() -> int:
    """Fetch and store the configured history range."""
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
    print(f"Session: {session.client_id} expires {session.expires_at}", flush=True)
    print(f"Profile: {profile['result']['clientId']}", flush=True)

    exchange = config["exchange"]
    master = InstrumentMaster(client)
    master.sync([exchange])
    instrument = master.search(config["symbol"], exchange=exchange).iloc[0]
    instrument_id = str(instrument["conid"])
    print(f"{config['symbol']} instrument ID: {instrument_id}", flush=True)

    from_date = datetime.fromisoformat(config["from_date"])
    to_date = datetime.fromisoformat(config["to_date"])
    payload = client.historical_data(
        exchange=exchange,
        instrument_id=instrument_id,
        interval=config["interval"],
        from_date=_fmt_date(from_date),
        to_date=_fmt_date(to_date),
    )
    frame = candles_to_frame(payload)
    if frame.empty:
        print(f"No bars returned for {config['symbol']}.", flush=True)
        return 1

    output_directory = ROOT / config["output_directory"]
    output_directory.mkdir(parents=True, exist_ok=True)
    output_path = output_directory / f"{config['symbol']}.parquet"
    frame.to_parquet(output_path, index=False)
    print(
        f"Saved {len(frame)} bars to {output_path} "
        f"({frame['ts'].iloc[0]} to {frame['ts'].iloc[-1]})",
        flush=True,
    )
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
