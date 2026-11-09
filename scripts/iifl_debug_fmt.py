"""Inspect the exact shape of an IIFL historical-data response."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from forward.brokers.iifl.auth import SessionStore
from forward.brokers.iifl.client import IiflClient, _fmt_date
from forward.brokers.iifl.contracts import InstrumentMaster
from forward.config.settings import get_settings

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "iifl_debug_format.json"


def main() -> int:
    """Print response metadata and a bounded sample."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    config_path = parser.parse_args().config
    config = json.loads(config_path.read_text(encoding="utf-8"))
    print(f"Configuration: {config_path.resolve()}", flush=True)

    settings = get_settings()
    store = SessionStore(settings.iifl_session_cache)
    session = store.load()
    if session is None:
        raise RuntimeError("No cached IIFL session is available")
    client = IiflClient(
        app_key=settings.iifl_app_key,
        app_secret=settings.iifl_app_secret,
        session_store=store,
    )
    client.set_session(session)
    master = InstrumentMaster(client)
    master.sync([config["exchange"]])
    instrument = master.search(config["symbol"], exchange=config["exchange"]).iloc[0]
    from_date = datetime.fromisoformat(config["from_date"])
    to_date = datetime.fromisoformat(config["to_date"])
    payload = client.historical_data(
        exchange=config["exchange"],
        instrument_id=str(instrument["conid"]),
        interval=config["interval"],
        from_date=_fmt_date(from_date),
        to_date=_fmt_date(to_date),
    )
    preview = json.dumps(payload, indent=2, default=str)
    print(f"Type: {type(payload)}")
    print(f"Keys: {list(payload.keys()) if isinstance(payload, dict) else 'N/A'}")
    print(f"Full response preview:\n{preview[:config['response_preview_characters']]}")
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
