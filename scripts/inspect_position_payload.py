"""Dump the raw position payloads so the prior-close question can be answered
from evidence rather than assumption.

Run with a live session (`.cache/iifl_session.json` must be current):
    ./.venv/Scripts/python.exe scripts/inspect_position_payload.py
"""
import argparse
import json
from pathlib import Path

from forward.api import main as M

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "inspect_position_payload.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    config_path = parser.parse_args().config
    config = json.loads(config_path.read_text(encoding="utf-8"))
    print(f"Configuration: {config_path.resolve()}")
    client = M._authed_client()
    try:
        payload = client.positions()
    finally:
        client.close()

    rows = M._broker_rows(payload)
    print(f"raw rows: {len(rows)}")
    if rows:
        # Show every key on the first row -- the whole point is to find out
        # whether a prior-close field exists under a name we did not guess.
        print("keys on row 0:", sorted(rows[0].keys()))
        print(
            json.dumps(rows[0], indent=2, default=str)[
                :config["payload_preview_characters"]
            ]
        )

    print("\n--- any key containing prev/close/open/yest ---")
    for row_index, row in enumerate(rows[:config["row_count"]]):
        hits = {k: v for k, v in row.items()
                if any(term in k.lower() for term in config["key_terms"])}
        print(f"  row {row_index}: {hits}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
