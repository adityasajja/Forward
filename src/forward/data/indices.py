"""Official index series, cached next to the stock history.

Why this exists: the stock cache holds *tradable* instruments downloaded from the
broker, and an index is not tradable, so the Nifty 50 was never in it. The market
overview then fell back to an ETF that tracks the index. The ETF's percentage moves
match, but its price is per unit (about 1/100th of the index), so the headline
level was wrong. IIFL does publish the index: ``INDICES.json`` lists it and
``/marketdata/historicaldata`` serves its candles.

Two properties matter:

* **The candles need a broker session once; reading them never does.** The series
  is downloaded whenever a session exists and cached as parquet, so the page works
  the rest of the time from the cache.
* **It lives in its own folder** (``iifl_daily/INDICES``), not beside the stocks.
  The scanners read every parquet under ``NSEEQ`` as a stock, and an index would
  show up in their rankings as a company called NIFTY50.
"""

from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal

import httpx
import numpy as np
import pandas as pd
from loguru import logger

NIFTY_50 = "NIFTY 50"
INDIA_VIX = "INDIA VIX"
COLUMNS = ["ts", "open", "high", "low", "close", "volume"]

# A cached index older than this is not shown as "the latest": the stock data it
# would sit beside it is newer.
MAX_STALE_DAYS = 5

def index_dir(data_root: Path) -> Path:
    return Path(data_root) / "iifl_daily" / "INDICES"


def index_path(data_root: Path, name: str = NIFTY_50) -> Path:
    return index_dir(data_root) / f"{name.replace(' ', '')}.parquet"


def load_index(data_root: Path, name: str = NIFTY_50) -> pd.DataFrame | None:
    """Return the cached series, or ``None`` when it is missing or stale."""
    path = index_path(data_root, name)
    if not path.exists():
        return None
    frame = pd.read_parquet(path)
    if frame.empty or len(frame) < 20 or not is_fresh(frame):
        return None
    return frame


def is_fresh(frame: pd.DataFrame, today: date | None = None) -> bool:
    today = today or date.today()
    last = pd.to_datetime(frame["ts"]).max().date()
    return (today - last).days <= MAX_STALE_DAYS


def synced_today(data_root: Path, name: str = NIFTY_50) -> bool:
    path = index_path(data_root, name)
    try:
        return path.exists() and date.fromtimestamp(path.stat().st_mtime) >= date.today()
    except OSError:
        return False


def _contract_rows(
    data_root: Path, base_url: str, source: Literal["CACHE", "HTTP"]
) -> list[dict[str, Any]]:
    """Read index contracts from exactly the selected source."""
    cache = Path(data_root) / "indices_contract.json"
    if source == "CACHE":
        if not cache.exists():
            raise FileNotFoundError(f"index contract cache does not exist: {cache}")
        rows = json.loads(cache.read_text(encoding="utf8"))
    elif source == "HTTP":
        resp = httpx.get(
            f"{base_url.rstrip('/')}/contractfiles/INDICES.json", timeout=20.0
        )
        resp.raise_for_status()
        rows = resp.json()
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(rows), encoding="utf8")
    else:
        raise ValueError(f"unsupported index contract source: {source!r}")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"invalid index contract rows from {source}")
    return rows


def _contract_row(
    data_root: Path,
    base_url: str,
    name: str,
    source: Literal["CACHE", "HTTP"],
) -> dict[str, Any] | None:
    matches = [
        row
        for row in _contract_rows(data_root, base_url, source)
        if str(row.get("underlyingInstrumentSymbol", "")).upper() == name.upper()
    ]
    if len(matches) > 1:
        raise ValueError(f"multiple IIFL index contracts match {name!r}")
    return matches[0] if matches else None


def sync_index(
    client: Any,
    data_root: Path,
    *,
    name: str,
    base_url: str,
    contract_source: Literal["CACHE", "HTTP"],
    interval: Literal["1d", "15m"],
    days: int = 800,
    force: bool = False,
) -> str:
    """Download index candles from one configured contract source and interval.

    Returns ``ok``, ``skip``, ``empty``, or ``fail``. ``client`` must hold a live
    session because ``historical_data`` is authenticated.
    """
    if not force and synced_today(data_root, name):
        return "skip"
    try:
        contract = _contract_row(data_root, base_url, name, contract_source)
        if contract is None:
            raise LookupError(f"no contract for index {name!r}")
        iid = str(contract["instrumentId"])
        exchange = str(contract.get("exchange", "")).upper()
        if not exchange:
            raise LookupError(f"no exchange for index {name!r}")
        today = date.today()
        candles: list[Any] = []
        start = (today - timedelta(days=days)).strftime("%d-%b-%Y")
        end = today.strftime("%d-%b-%Y")
        raw = client.historical_data(exchange, iid, interval, start, end)
        candles = raw["result"][0]["candles"]
        if interval == "15m":
            candles = _daily_from_intraday(candles)
        frame = pd.DataFrame(candles, columns=COLUMNS)
        if frame.empty:
            return "empty"
        frame["ts"] = pd.to_datetime(frame["ts"])
        _write(data_root, name, frame, source="iifl")
        return "ok"
    except Exception as exc:  # noqa: BLE001 - a failed refresh keeps the old file
        logger.warning("index {} sync failed: {}", name, str(exc)[:160])
        return "fail"


def _daily_from_intraday(candles: list[Any]) -> list[list[Any]]:
    """Fold intraday candles into one bar per session: first open, extremes, last close."""
    frame = pd.DataFrame(candles, columns=COLUMNS)
    frame["ts"] = pd.to_datetime(frame["ts"])
    frame = frame.sort_values("ts")
    frame["day"] = frame["ts"].dt.normalize()
    daily = frame.groupby("day").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", lambda values: values.sum() if values.notna().all() else None),
    )
    daily.index.name = "ts"
    return daily.reset_index()[COLUMNS].values.tolist()


def _write(data_root: Path, name: str, frame: pd.DataFrame, *, source: str) -> None:
    if frame["ts"].isna().any() or frame.duplicated("ts").any():
        raise ValueError(f"index {name} has missing or duplicate timestamps")
    prices = frame[["open", "high", "low", "close"]].to_numpy(dtype=float)
    if not np.isfinite(prices).all() or not (prices > 0).all():
        raise ValueError(f"index {name} has invalid OHLC prices")
    if (
        (prices[:, 1] < prices[:, [0, 3]].max(axis=1)).any()
        or (prices[:, 2] > prices[:, [0, 3]].min(axis=1)).any()
        or (prices[:, 1] < prices[:, 2]).any()
    ):
        raise ValueError(f"index {name} has inconsistent OHLC ranges")
    frame["volume"] = pd.to_numeric(frame["volume"], errors="raise")
    volume = frame["volume"].dropna().to_numpy(dtype=float)
    if not np.isfinite(volume).all() or (volume < 0).any():
        raise ValueError(f"index {name} has invalid volume")
    frame = frame.sort_values("ts")
    path = index_path(data_root, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    frame.to_parquet(tmp, index=False)
    os.replace(tmp, path)  # readers never see a half-written file
    path.with_suffix(".source").write_text(source, encoding="utf8")
    logger.info("index {}: cached {} daily bars from {}", name, len(frame), source)
