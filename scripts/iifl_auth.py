"""Authenticate with IIFL and fetch the configured minute-data sample.

The callback and sample request details live in
``config/iifl_auth_fetch.json``. Broker credentials and session storage use
Forward application settings.
"""

from __future__ import annotations

import argparse
import http.server
import json
import socketserver
import threading
import time
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

from forward.brokers.iifl.auth import Session, SessionStore
from forward.brokers.iifl.client import IiflClient, _fmt_date
from forward.brokers.iifl.contracts import InstrumentMaster
from forward.brokers.iifl.feeds import candles_to_frame
from forward.brokers.iifl.session import IiflSessionAdapter
from forward.config.settings import get_settings

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "iifl_auth_fetch.json"

_auth_code: str | None = None
_client_id_callback: str | None = None
_callback_event = threading.Event()


class CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Capture the documented IIFL authorization callback fields."""

    def do_GET(self) -> None:
        """Store callback credentials and return a browser status page."""
        global _auth_code, _client_id_callback
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        auth_code = params.get("authcode", [None])[0]
        client_id = params.get("clientid", [None])[0]
        if auth_code and client_id:
            _auth_code = auth_code
            _client_id_callback = client_id
            _callback_event.set()
            body = b"<html><body><h2>Login successful</h2><p>You can close this tab.</p></body></html>"
            self.send_response(200)
        else:
            body = b"<html><body><h2>Login failed</h2><p>No auth code received.</p></body></html>"
            self.send_response(400)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        """Keep OAuth query credentials out of server logs."""


def start_callback_server(config: dict) -> socketserver.TCPServer:
    """Start the callback server on its configured interface and port.

    Args:
        config: Explicit callback settings.

    Returns:
        The running callback server.
    """
    server = socketserver.TCPServer(
        (config["callback_host"], config["callback_port"]),
        CallbackHandler,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(
        f"Callback server listening on {config['callback_host']}:{config['callback_port']}",
        flush=True,
    )
    return server


def authenticate(config: dict, settings) -> Session | None:
    """Reuse a saved IIFL session or complete the configured browser login.

    Args:
        config: Explicit callback settings.
        settings: Forward settings with IIFL credentials and session path.

    Returns:
        The active IIFL session, or ``None`` after a callback timeout.
    """
    session_store = SessionStore(settings.iifl_session_cache)
    session = session_store.load()
    if session is not None:
        print(f"Using session for {session.client_id}, expires {session.expires_at}", flush=True)
        return session

    redirect_url = (
        f"http://{config['callback_host']}:{config['callback_port']}"
    )
    session_adapter = IiflSessionAdapter(
        app_key=settings.iifl_app_key,
        app_secret=settings.iifl_app_secret,
        base_url=settings.iifl_base_url,
        session_path=settings.iifl_session_cache,
        redirect_url=redirect_url,
    )
    login_url = session_adapter.status()["login_url"]
    server = start_callback_server(config)
    print(f"Open this URL in your browser:\n  {login_url}", flush=True)
    print("Log in with your IIFL trading credentials and OTP.", flush=True)
    try:
        if not _callback_event.wait(timeout=config["callback_wait_seconds"]):
            print("Timed out waiting for the IIFL callback.", flush=True)
            return None
        if _client_id_callback is None or _auth_code is None:
            raise RuntimeError("IIFL callback did not provide clientid and authcode")
        session_adapter.create_session(_client_id_callback, _auth_code)
        session = session_store.load()
        if session is None:
            raise RuntimeError("IIFL session creation did not save an active session")
        print(f"Session created for {session.client_id}, expires {session.expires_at}", flush=True)
        return session
    finally:
        server.shutdown()
        server.server_close()


def fetch_1min_ohlcv(
    client: IiflClient,
    master: InstrumentMaster,
    symbol: str,
    config: dict,
) -> int:
    """Fetch configured one-minute candles for one symbol.

    Args:
        client: Authenticated IIFL client.
        master: Synced instrument master.
        symbol: Instrument symbol to fetch.
        config: Explicit date, interval, exchange, and chunk settings.

    Returns:
        Number of candles returned by the broker.
    """
    exchange = config["exchange"]
    instruments = master.search(symbol, exchange=exchange)
    if instruments.empty:
        print(f"No instrument found for {symbol} on {exchange}", flush=True)
        return 0

    instrument_id = str(instruments.iloc[0]["conid"])
    from_date = datetime.fromisoformat(config["from_date"])
    to_date = datetime.fromisoformat(config["to_date"])
    print(
        f"Fetching {symbol} ({instrument_id}, {exchange}, {config['interval']}) "
        f"{from_date.date()} to {to_date.date()}...",
        flush=True,
    )
    total_candles = 0
    chunk_start = from_date
    while chunk_start < to_date:
        chunk_end = min(
            chunk_start + timedelta(days=config["chunk_days"]),
            to_date,
        )
        payload = client.historical_data(
            exchange=exchange,
            instrument_id=instrument_id,
            interval=config["interval"],
            from_date=_fmt_date(chunk_start),
            to_date=_fmt_date(chunk_end),
        )
        frame = candles_to_frame(payload)
        total_candles += len(frame)
        chunk_start = chunk_end + timedelta(days=1)
        print(f"  Got {len(frame)} candles through {chunk_end.date()}", flush=True)
        time.sleep(config["pause_between_chunks_seconds"])

    print(f"Total: {total_candles} candles for {symbol}", flush=True)
    return total_candles


def main() -> int:
    """Run the configured IIFL login and data-fetch workflow."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    config_path = parser.parse_args().config
    config = json.loads(config_path.read_text(encoding="utf-8"))
    print(f"Configuration: {config_path.resolve()}", flush=True)
    settings = get_settings()
    session = authenticate(config, settings)
    if session is None:
        return 1

    client = IiflClient(
        app_key=settings.iifl_app_key,
        app_secret=settings.iifl_app_secret,
        base_url=settings.iifl_base_url,
    )
    client.set_session(session)
    master = InstrumentMaster(client)
    master.sync([config["exchange"]])
    try:
        for symbol in config["symbols"]:
            try:
                fetch_1min_ohlcv(client, master, symbol, config)
            except Exception as exc:
                print(f"{symbol}: fetch failed: {exc}", flush=True)
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
