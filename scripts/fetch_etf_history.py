"""Download long daily history for NSE ETFs from the public chart feed.

The broker cache holds only four ETFs with real history, and three of them track
almost the same exposure (Nifty 50 twice, plus Next 50 and Bank). Rotation needs
assets that move apart from each other — gold, offshore equity, cash — so this
fills the gap from the same public source the nightly top-up uses.

Written to ``data/iifl_daily/ETF`` rather than beside the stocks: everything under
``NSEEQ`` is treated as a tradable share by the scanners, and an ETF would show up
in their rankings as a company.

Run:  ./.venv/Scripts/python.exe scripts/fetch_etf_history.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

import httpx
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "fetch_etf_history.json"
COLUMNS = ["ts", "open", "high", "low", "close", "volume"]


def fetch(
    ticker: str,
    config: dict,
    output_directory: Path,
) -> tuple[str, str, int]:
    """Fetch one ETF history and return its status.

    Args:
        ticker: ETF symbol to fetch.
        config: Explicit settings loaded from ``config/fetch_etf_history.json``.
        output_directory: Directory where the parquet file is written.

    Returns:
        The ticker, a status message, and the number of bars received.
    """
    try:
        now = pd.Timestamp.now(config["timezone"])
        # Explicit epoch bounds, not ``range=max``: asked for the whole history by
        # range, the feed quietly answers in monthly bars.
        resp = httpx.get(
            config["url_template"].format(symbol=quote(f"{ticker}.NS")),
            params={
                "period1": int(pd.Timestamp(config["start_date"], tz=config["timezone"]).timestamp()),
                "period2": int(now.timestamp()),
                "interval": config["interval"],
            },
            headers={"User-Agent": config["user_agent"]},
            timeout=config["request_timeout_seconds"],
        )
        if resp.status_code != 200:
            return ticker, f"http {resp.status_code}", 0
        result = resp.json()["chart"]["result"]
        if not result:
            return ticker, "empty", 0
        result = result[0]
        q = result["indicators"]["quote"][0]
        offset = int(result["meta"].get("gmtoffset", 19800))
        frame = pd.DataFrame(
            {
                "ts": (
                    pd.to_datetime(result["timestamp"], unit="s") + pd.to_timedelta(offset, unit="s")
                ).normalize()
                + pd.Timedelta(config["bar_open"] + ":00"),
                "open": q["open"],
                "high": q["high"],
                "low": q["low"],
                "close": q["close"],
                "volume": q["volume"],
            }
        ).dropna(subset=["open", "high", "low", "close"])
        if frame.empty:
            return ticker, "no bars", 0
        frame["volume"] = frame["volume"].fillna(0)
        frame[["open", "high", "low", "close"]] = frame[["open", "high", "low", "close"]].round(4)
        # A same-day partial bar would be treated as a finished session downstream.
        frame = frame[frame["ts"].dt.date < now.date()]
        frame = frame.drop_duplicates(subset="ts").sort_values("ts").reset_index(drop=True)
        output_directory.mkdir(parents=True, exist_ok=True)
        frame[COLUMNS].to_parquet(output_directory / f"{ticker}.parquet", index=False)
        return ticker, "ok", len(frame)
    except Exception as exc:  # noqa: BLE001 - report and continue
        return ticker, f"fail: {str(exc)[:60]}", 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    config_path = parser.parse_args().config
    config = json.loads(config_path.read_text(encoding="utf-8"))
    print(f"Configuration: {config_path.resolve()}")
    output_directory = ROOT / config["output_directory"]
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=config["max_workers"]) as pool:
        results = sorted(
            pool.map(
                lambda ticker: fetch(ticker, config, output_directory),
                config["etfs"],
            ),
            key=lambda result: -result[2],
        )
    ok = [r for r in results if r[1] == "ok"]
    for ticker, status, bars in results:
        if status == "ok":
            df = pd.read_parquet(
                output_directory / f"{ticker}.parquet", columns=["ts", "volume"]
            )
            span = f"{df.ts.iloc[0].date()} -> {df.ts.iloc[-1].date()}"
            med = int(df.volume.tail(120).median())
            print(
                f"{ticker:<12} {bars:>5} bars  {span}  medvol {med:>12,}  "
                f"{config['etfs'][ticker]}"
            )
        else:
            print(f"{ticker:<12} {'':>5}       {status}")
    print(
        f"\n{len(ok)}/{len(results)} downloaded in {time.monotonic() - t0:.1f}s "
        f"-> {output_directory}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
