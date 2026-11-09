"""Import explicitly requested Yahoo daily bars into a separate public-source cache."""

from __future__ import annotations

import math
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import pandas as pd
from loguru import logger

from forward.core.session import IST

_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_CLOSE_AFTER = (16, 30)  # IST: after the close, once the day's bar is final
_MAX_JUMP = 0.35  # a new close this far from the last stored one is treated as suspect
_BAR_OPEN = pd.Timedelta(hours=9, minutes=15)
COLUMNS = ["ts", "open", "high", "low", "close", "volume"]


def yahoo_cache_dir(data_root: Path) -> Path:
    """Return the cache reserved for Yahoo public daily bars."""
    return Path(data_root) / "public_daily" / "YAHOO" / "NSEEQ"


def _yahoo_symbol(stem: str) -> str:
    base = stem[:-3] if stem.upper().endswith("-EQ") else stem
    return f"{base}.NS"


def fetch_recent(stem: str, *, now: datetime | None = None) -> pd.DataFrame | None:
    """The last month of daily bars, or None if unavailable. Today's bar is dropped until the close."""
    now = now or datetime.now(IST)
    resp = httpx.get(
        _URL.format(symbol=quote(_yahoo_symbol(stem))),
        params={"range": "1mo", "interval": "1d"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=15.0,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    result = resp.json()["chart"]["result"]
    if not result:
        return None
    result = result[0]
    quote_ = result["indicators"]["quote"][0]
    offset = int(result["meta"].get("gmtoffset", 19800))
    frame = pd.DataFrame(
        {
            "ts": (pd.to_datetime(result["timestamp"], unit="s") + pd.to_timedelta(offset, unit="s")).normalize()
            + _BAR_OPEN,
            "open": quote_["open"],
            "high": quote_["high"],
            "low": quote_["low"],
            "close": quote_["close"],
            "volume": quote_["volume"],
        }
    )
    if frame[["open", "high", "low", "close"]].isna().any().any():
        raise ValueError(f"Yahoo returned incomplete OHLC bars for {stem}")
    prices = frame[["open", "high", "low", "close"]].to_numpy(dtype=float)
    if not pd.notna(prices).all() or not (prices > 0).all():
        raise ValueError(f"Yahoo returned invalid OHLC prices for {stem}")
    if ((prices[:, 1] < prices[:, [0, 3]].max(axis=1)) | (prices[:, 2] > prices[:, [0, 3]].min(axis=1)) | (prices[:, 1] < prices[:, 2])).any():
        raise ValueError(f"Yahoo returned inconsistent OHLC ranges for {stem}")
    frame[["open", "high", "low", "close"]] = frame[["open", "high", "low", "close"]].astype(float).round(2)
    frame["volume"] = pd.to_numeric(frame["volume"], errors="raise").astype("Float64")
    present_volume = frame["volume"].dropna().astype(float)
    if (present_volume < 0).any() or not present_volume.map(math.isfinite).all():
        raise ValueError(f"Yahoo returned invalid volume for {stem}")
    if (now.hour, now.minute) < _CLOSE_AFTER:
        frame = frame[frame["ts"].dt.date < now.date()]
    return frame


def append_new_bars(path: Path, fresh: pd.DataFrame) -> str:
    """Append bars newer than the file's last one.

    Returns:
        ``ok``, ``current``, or ``suspect``.
    """
    existing = pd.read_parquet(path)
    if existing.empty:
        raise ValueError(f"Yahoo cache has no rows: {path}")
    last_day = existing["ts"].iloc[-1].normalize()
    new = fresh[fresh["ts"].dt.normalize() > last_day]
    if new.empty:
        return "current"
    last_close = float(existing["close"].iloc[-1])
    if last_close > 0 and abs(float(new["close"].iloc[0]) / last_close - 1.0) > _MAX_JUMP:
        return "suspect"
    new = new[COLUMNS].copy()
    for column in COLUMNS:
        if column != "volume":
            new[column] = new[column].astype(existing[column].dtype)
    merged = pd.concat([existing[COLUMNS], new], ignore_index=True)
    tmp = path.with_suffix(".parquet.tmp")
    merged.to_parquet(tmp, index=False)
    os.replace(tmp, path)
    return "ok"


def refresh(
    data_root: Path,
    symbols: list[str],
    *,
    workers: int = 6,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Append explicit Yahoo bars to the separate public cache for the given symbols."""
    root = yahoo_cache_dir(data_root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "source.json").write_text(
        '{"source":"YAHOO_PUBLIC_DAILY"}', encoding="utf8"
    )
    stems = symbols
    counts = {"ok": 0, "current": 0, "suspect": 0, "fail": 0, "missing": 0}
    failed_symbols: list[str] = []
    missing_symbols: list[str] = []

    def one(stem: str) -> str:
        path = root / f"{stem}.parquet"
        if not path.exists():
            return "missing"
        try:
            fresh = fetch_recent(stem, now=now)
        except Exception as exc:  # noqa: BLE001 - offline, rate-limited or the endpoint changed
            logger.debug("eod fetch failed for {}: {}", stem, str(exc)[:120])
            return "fail"
        if fresh is None or fresh.empty:
            return "fail"
        return append_new_bars(path, fresh)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for stem, outcome in zip(stems, pool.map(one, stems), strict=True):
            counts[outcome] += 1
            if outcome == "fail":
                failed_symbols.append(stem)
            if outcome == "missing":
                missing_symbols.append(stem)

    summary = {
        "ran_at": datetime.now(UTC).isoformat(),
        "ran_date_ist": (now or datetime.now(IST)).date().isoformat(),
        "names": len(stems),
        "symbols": sorted({s.removesuffix("-EQ") for s in stems}),
        "failed_symbols": failed_symbols,
        "missing_symbols": missing_symbols,
        "after_close": ((now or datetime.now(IST)).hour, (now or datetime.now(IST)).minute) >= _CLOSE_AFTER,
        **counts,
    }
    logger.info("eod refresh: {}", {k: v for k, v in summary.items() if k != "symbols"})
    return summary
