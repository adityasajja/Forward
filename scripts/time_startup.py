"""Measure server startup and response time using a named config."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "time_startup.json"


def main() -> int:
    """Measure startup delay and repeated response time."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    config_path = parser.parse_args().config
    config = json.loads(config_path.read_text(encoding="utf-8"))
    print(f"Configuration: {config_path.resolve()}")
    executable = ROOT / ".venv" / "Scripts" / "forward.exe"
    port = str(config["port"])
    url = f"http://127.0.0.1:{port}/"
    started_at = time.time()
    process = subprocess.Popen(
        [str(executable), "serve", "--no-browser", "--port", port],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        while time.time() - started_at < config["startup_timeout_seconds"]:
            try:
                urllib.request.urlopen(url, timeout=config["connection_timeout_seconds"])
                print(f"ready after {time.time() - started_at:.1f}s")
                break
            except Exception:  # noqa: BLE001 - server may not yet be ready
                time.sleep(config["startup_poll_seconds"])
        else:
            raise TimeoutError(
                f"Server did not become ready within {config['startup_timeout_seconds']} seconds."
            )

        worst_seconds = 0.0
        end_time = time.time() + config["watch_seconds"]
        request_count = 0
        while time.time() < end_time:
            request_started = time.time()
            try:
                urllib.request.urlopen(url, timeout=config["request_timeout_seconds"])
            except Exception as exc:  # noqa: BLE001 - report every failed request
                print("request failed:", exc)
            worst_seconds = max(worst_seconds, time.time() - request_started)
            request_count += 1
            time.sleep(config["request_poll_seconds"])
        print(
            f"{request_count} requests over {config['watch_seconds']:.0f}s, "
            f"slowest {worst_seconds * 1000:.0f} ms"
        )
        return 0
    finally:
        process.terminate()
        process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
