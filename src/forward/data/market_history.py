"""Daily history for the parts of the Indian market the broker cache does not carry.

The IIFL cache holds broker history. Exchange downloads are kept separately under
``public_daily`` and never repair or merge into that broker cache:

* ``public_daily/BSEEQ/<SYMBOL>.parquet``  every BSE-listed stock that is **not** also on NSE
  (a dual-listed company stays under NSE, where it is more liquid, and keeping both would give
  one symbol two exchanges).
* ``public_daily/NSESME/<SYMBOL>.parquet``  NSE Emerge (SME) stocks.
* ``instruments_names.csv``  company name, ISIN and series for those symbols, so a search by
  name finds them.

Prices are corporate-action adjusted for splits and bonuses with a structural detector: the
exchanges' previous-close field is *not* adjusted, so a split shows up as a price cliff, and a
day whose entire range sits outside the +-20% price band cannot be an ordinary move. Raw day
files are kept under ``bse_bhav/`` and ``nse_bhav/`` so the history can be rebuilt without
downloading it again.
"""

from __future__ import annotations

import io
import json
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pandas as pd
from loguru import logger

BSE_URL = "https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{d:%Y%m%d}_F_0000.CSV"
NSE_URL = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{d:%d%m%Y}.csv"
NSE_LISTS = (
    "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
    "https://nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv",
)
BSE_EXCHANGE = "BSEEQ"
SME_EXCHANGE = "NSESME"
# The configured BSE UDiFF endpoint has no legacy-schema chain for earlier sessions.
#: BSE series that are equity (A/B groups, trade-for-trade T/Z/X/XT, SME M/MT/MS, and the rest);
#: F (debentures), G (government securities), IF (InvITs), E, P, R (rights entitlements) and W (warrants)
#: are not stocks; partly-paid shares (ISIN IN9...) are dropped in `rebuild`.
BSE_EQUITY_SERIES = frozenset({"A", "B", "X", "XT", "M", "MT", "MS", "T", "Z", "ZP"})
NSE_SME_SERIES = frozenset({"SM", "ST", "SZ"})
#: NSE series kept from the daily file: main board (EQ, BE trade-for-trade, BZ) and SME.
NSE_KEEP_SERIES = frozenset({"EQ", "BE", "BZ", "SM", "ST", "SZ"})
NSE_EXTRA_MANIFEST = "market_history_nse_extra.json"
_BAR_OPEN = pd.Timedelta(hours=9, minutes=15)
_NICE = np.array([1 / 2, 1 / 3, 2 / 3, 1 / 4, 3 / 4, 1 / 5, 2 / 5, 3 / 5, 4 / 5, 1 / 10, 1 / 20, 1 / 50, 1 / 100, 3 / 2, 2.0, 3.0, 4.0, 5.0, 10.0, 20.0, 5 / 3, 5 / 4, 5 / 2, 5 / 8, 8 / 5])
_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.bseindia.com/"}
COLUMNS = ["ts", "open", "high", "low", "close", "volume"]
_RAW = ["symbol", "code", "isin", "name", "series", "open", "high", "low", "close", "prev", "volume", "turnover", "trades"]


# ─────────────────────────────── downloading ───────────────────────────────
def _get(
    url: str,
    headers: dict[str, str],
    request_timeout_seconds: float,
) -> httpx.Response:
    """Fetch one exchange response and raise immediately on transport errors."""
    return httpx.get(
        url,
        headers=headers,
        timeout=request_timeout_seconds,
        follow_redirects=True,
    )


def _fetch_bse(d: date, request_timeout_seconds: float) -> pd.DataFrame | None:
    """Fetch one BSE UDiFF session, or return ``None`` for an HTTP 404 holiday."""
    r = _get(BSE_URL.format(d=d), _HEADERS, request_timeout_seconds)
    if r.status_code == 404:
        return None
    if r.status_code != 200 or not r.content.startswith(b"TradDt"):
        raise RuntimeError(f"bse bhavcopy {d}: invalid UDiFF response (HTTP {r.status_code})")
    x = pd.read_csv(io.StringIO(r.text))
    x = x[x["SctySrs"].isin(BSE_EQUITY_SERIES)]
    return pd.DataFrame(
        {
            "symbol": x["TckrSymb"].astype(str).str.strip().str.upper(),
            "code": x["FinInstrmId"].astype(str),
            "isin": x["ISIN"].astype(str),
            "name": x["FinInstrmNm"].astype(str).str.strip(),
            "series": x["SctySrs"].astype(str),
            "open": x["OpnPric"], "high": x["HghPric"], "low": x["LwPric"], "close": x["ClsPric"],
            "prev": x["PrvsClsgPric"], "volume": x["TtlTradgVol"], "turnover": x["TtlTrfVal"], "trades": x["TtlNbOfTxsExctd"],
        }
    )


def _fetch_nse(d: date, request_timeout_seconds: float) -> pd.DataFrame | None:
    r = _get(
        NSE_URL.format(d=d),
        {"User-Agent": "Mozilla/5.0"},
        request_timeout_seconds,
    )
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        raise RuntimeError(f"nse bhavcopy {d}: HTTP {r.status_code}")
    if not r.content.lstrip().startswith(b"SYMBOL"):
        raise RuntimeError(f"nse bhavcopy {d}: not a CSV")  # a placeholder page: retried next run, never recorded as a holiday
    x = pd.read_csv(io.StringIO(r.text), skipinitialspace=True)
    x.columns = [c.strip() for c in x.columns]
    x["SERIES"] = x["SERIES"].astype(str).str.strip()
    x = x[x["SERIES"].isin(NSE_KEEP_SERIES)]
    return pd.DataFrame(
        {
            "symbol": x["SYMBOL"].astype(str).str.strip().str.upper(), "code": "", "isin": "", "name": "", "series": x["SERIES"],
            "open": x["OPEN_PRICE"], "high": x["HIGH_PRICE"], "low": x["LOW_PRICE"], "close": x["CLOSE_PRICE"],
            "prev": x["PREV_CLOSE"], "volume": x["TTL_TRD_QNTY"], "turnover": x["TURNOVER_LACS"], "trades": x["NO_OF_TRADES"],
        }
    )


def _download_days(
    folder: Path,
    fetch: Callable[[date, float], pd.DataFrame | None],
    days: list[date],
    workers: int,
    request_timeout_seconds: float,
) -> int:
    """Fetch the days not on disk yet. A holiday is remembered with an empty marker file."""
    folder.mkdir(parents=True, exist_ok=True)
    todo = [d for d in days if d.weekday() < 5 and not (folder / f"{d}.parquet").exists() and not (folder / f"{d}.holiday").exists()]
    got = 0

    def one(d: date) -> bool:
        frame = fetch(d, request_timeout_seconds)
        if frame is None or frame.empty:
            (folder / f"{d}.holiday").write_text("")
            return False
        frame.to_parquet(folder / f"{d}.parquet", index=False)
        return True

    if todo:
        with ThreadPoolExecutor(workers) as pool:
            got = sum(pool.map(one, todo))
        logger.info("{}: {} new session file(s) of {} requested", folder.name, got, len(todo))
    return got


def _prune_nse_duplicates(folder: Path) -> None:
    """NSE serves a copy of the previous session for holidays; drop those so they are not a day."""
    prev = None
    for f in sorted(folder.glob("*.parquet")):
        cur = pd.read_parquet(f, columns=["symbol", "close"]).drop_duplicates("symbol").set_index("symbol")["close"]
        if prev is not None and len(cur):
            joined = cur.reindex(prev.index)
            if ((joined.values == prev.values) | np.isnan(joined.values)).mean() > 0.97:
                f.rename(f.with_suffix(".holiday"))
                continue
        prev = cur


# ─────────────────────────────── building ───────────────────────────────
def _snap(x: float) -> float:
    j = int(np.argmin(np.abs(np.log(_NICE) - np.log(x))))
    return float(_NICE[j]) if abs(np.log(_NICE[j] / x)) < 0.22 else x


def _adjust(g: pd.DataFrame) -> pd.DataFrame:
    """Split/bonus-adjust one symbol's raw sessions (see the module note)."""
    g = g.sort_values("date").reset_index(drop=True)
    contiguous = ((g["prev"] - g["close"].shift()).abs() <= 0.01 * g["close"].shift()).to_numpy()
    mid = (g["high"] + g["low"]) / 2
    cliff = (((g["high"] / g["prev"]) < 0.80) | ((g["low"] / g["prev"]) > 1.25)).to_numpy() & contiguous
    ratios = np.ones(len(g))
    for i in np.where(cliff)[0]:
        ratios[i] = _snap(float(mid.iloc[i] / g["prev"].iloc[i]))
    factor = np.ones(len(g))
    run = 1.0
    for i in range(len(g) - 1, -1, -1):
        factor[i] = run
        run *= ratios[i]
    for c in ("open", "high", "low", "close"):
        g[c] = (g[c] * factor).round(2)
    g["volume"] = (g["volume"] / factor).round().astype("int64")
    return g


def _read_days(folder: Path) -> pd.DataFrame:
    parts = []
    for f in sorted(folder.glob("*.parquet")):
        d = pd.read_parquet(f)
        d["date"] = pd.Timestamp(f.stem)
        parts.append(d)
    if not parts:
        return pd.DataFrame(columns=[*_RAW, "date"])
    raw = pd.concat(parts, ignore_index=True)
    prices = raw[["open", "high", "low", "close"]].to_numpy(dtype=float)
    volume = pd.to_numeric(raw["volume"], errors="raise").to_numpy(dtype=float)
    valid_prices = np.isfinite(prices).all(axis=1) & (prices > 0).all(axis=1)
    valid_ranges = (
        (prices[:, 1] >= prices[:, [0, 3]].max(axis=1))
        & (prices[:, 2] <= prices[:, [0, 3]].min(axis=1))
        & (prices[:, 1] >= prices[:, 2])
    )
    if (
        not valid_prices.all()
        or not valid_ranges.all()
        or not np.isfinite(volume).all()
        or (volume < 0).any()
    ):
        raise ValueError(f"exchange history contains malformed OHLCV rows: {folder}")
    return raw.drop_duplicates(["symbol", "code", "date"], keep="last")


def _replace(tmp: Path, dst: Path, attempts: int = 6) -> bool:
    """Atomic replace that survives a reader holding the file open (Windows refuses the rename then)."""
    import time

    for i in range(attempts):
        try:
            os.replace(tmp, dst)
            return True
        except PermissionError:
            time.sleep(0.2 * (i + 1))
    logger.warning("could not replace {} (in use); left as it was", dst.name)
    tmp.unlink(missing_ok=True)
    return False


def _daily_bars(sessions: pd.DataFrame) -> pd.DataFrame:
    """Build the shared timestamp and OHLCV shape from exchange sessions.

    Args:
        sessions: Rows with a ``date`` column and open, high, low, close, and
            volume values.

    Returns:
        A frame in the application's daily-bar column order.
    """
    return pd.DataFrame(
        {
            "ts": sessions["date"] + _BAR_OPEN,
            **{column: sessions[column] for column in COLUMNS[1:]},
        }
    )[COLUMNS]


def _write_symbol(folder: Path, symbol: str, g: pd.DataFrame) -> None:
    frame = _daily_bars(g)
    frame["ts"] = frame["ts"].astype("datetime64[us]")
    tmp = folder / f"{symbol}.parquet.tmp"
    frame.to_parquet(tmp, index=False)
    _replace(tmp, folder / f"{symbol}.parquet")


def _nse_listings(request_timeout_seconds: float) -> pd.DataFrame:
    """Symbol, name, ISIN of every NSE-listed company (main board and SME)."""
    parts = []
    for url in NSE_LISTS:
        try:
            r = httpx.get(
                url,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=request_timeout_seconds,
                follow_redirects=True,
            )
            r.raise_for_status()
            d = pd.read_csv(io.StringIO(r.text))
            d.columns = [c.strip().replace("_", " ") for c in d.columns]
            parts.append(
                pd.DataFrame(
                    {
                        "symbol": d["SYMBOL"].astype(str).str.strip().str.upper(),
                        "name": d["NAME OF COMPANY"].astype(str).str.strip(),
                        "isin": d["ISIN NUMBER"].astype(str).str.strip(),
                        "series": d["SERIES"].astype(str).str.strip(),
                        "sme": "SME" in url,
                    }
                )
            )
        except Exception as exc:
            raise RuntimeError(f"could not read required NSE listing {url.rsplit('/', 1)[-1]}") from exc
    return pd.concat(parts, ignore_index=True)


def rebuild(data_root: Path, *, request_timeout_seconds: float) -> dict[str, int]:
    """Rewrite the BSEEQ and NSESME caches and the names file from the raw session files."""
    data_root = Path(data_root)
    daily = data_root / "public_daily"
    broker_daily = data_root / "iifl_daily"
    listings = _nse_listings(request_timeout_seconds)
    nse_isins = set(listings["isin"]) - {""}
    nse_symbols = {
        p.stem.removesuffix("-EQ").upper()
        for p in (broker_daily / "NSEEQ").glob("*.parquet")
    } | set(listings["symbol"])
    names: list[dict[str, Any]] = []
    counts = {BSE_EXCHANGE: 0, SME_EXCHANGE: 0, "NSEEQ_added": 0}

    written_bse: set[str] = set()
    written_sme: set[str] = set()
    # --- BSE: only companies that are not also listed on NSE
    bse = _read_days(data_root / "bse_bhav")
    if not bse.empty:
        # Pre-2024 sessions come from the legacy file, which has only the scrip code: take the ticker,
        # ISIN and name from the newest session that has them. A scrip delisted before then has none and is skipped.
        directory = bse[bse["symbol"] != ""].sort_values("date").groupby("code").tail(1).set_index("code")[["symbol", "isin", "name", "series"]]
        for col in ("symbol", "isin", "name"):
            bse[col] = bse["code"].map(directory[col])
        bse = bse.dropna(subset=["symbol"])
        bse = bse[~bse["isin"].isin(nse_isins)]
        # Partly-paid shares carry an IN9... ISIN and a PP suffix; they are not the stock itself.
        bse = bse[~bse["isin"].str.startswith("IN9") & ~bse["symbol"].str.contains(r"(?:PP|-RE|WARR)$", regex=True)]
        out = daily / BSE_EXCHANGE
        out.mkdir(parents=True, exist_ok=True)
        # A ticker that already means a different NSE company is replaced by the BSE scrip code.
        last = bse.sort_values("date").groupby("code").tail(1).set_index("code")
        for code, g in bse.groupby("code", sort=False):
            if len(g) < 5:
                continue
            row = last.loc[code]
            symbol = str(row["symbol"])
            if symbol in nse_symbols:
                symbol = str(code)
            _write_symbol(out, symbol, _adjust(g))
            names.append({"symbol": symbol, "exchange": BSE_EXCHANGE, "name": row["name"], "isin": row["isin"], "series": row["series"]})
            counts[BSE_EXCHANGE] += 1
            written_bse.add(symbol)

    # --- NSE: SME stocks get their own exchange; main-board stocks the broker cache lacks are added to NSEEQ
    nse = _read_days(data_root / "nse_bhav")
    if not nse.empty:
        info = listings.drop_duplicates("symbol").set_index("symbol")
        sme = nse[nse["series"].isin(NSE_SME_SERIES)]
        out = daily / SME_EXCHANGE
        out.mkdir(parents=True, exist_ok=True)
        for symbol, g in sme.groupby("symbol", sort=False):
            if len(g) < 5:
                continue
            _write_symbol(out, str(symbol), _adjust(g))
            meta = info.loc[symbol] if symbol in info.index else None
            names.append({"symbol": symbol, "exchange": SME_EXCHANGE, "name": meta["name"] if meta is not None else "", "isin": meta["isin"] if meta is not None else "", "series": g["series"].iloc[-1]})
            counts[SME_EXCHANGE] += 1
            written_sme.add(str(symbol))

        manifest_path = data_root / NSE_EXTRA_MANIFEST
        mine = set(json.loads(manifest_path.read_text(encoding="utf8"))) if manifest_path.exists() else set()
        if not all(isinstance(symbol, str) for symbol in mine):
            raise ValueError(f"invalid public NSE manifest: {manifest_path}")
        broker = {
            p.stem.removesuffix("-EQ").upper()
            for p in (broker_daily / "NSEEQ").glob("*.parquet")
        }
        broker |= {
            p.stem.removesuffix("-EQ").upper()
            for p in (daily / "NSEEQ").glob("*.parquet")
            if p.stem not in mine
        }
        main = nse[~nse["series"].isin(NSE_SME_SERIES)]
        out = daily / "NSEEQ"
        written: list[str] = []
        for symbol, g in main.groupby("symbol", sort=False):
            symbol = str(symbol)
            if symbol in broker or len(g) < 5:
                continue
            _write_symbol(out, symbol, _adjust(g))
            written.append(symbol)
        manifest_path.write_text(json.dumps(sorted(written)), encoding="utf8")
        counts["NSEEQ_added"] = len(written)

    for exchange, keep in ((BSE_EXCHANGE, written_bse), (SME_EXCHANGE, written_sme)):
        folder = daily / exchange
        if keep and folder.is_dir():  # these two folders belong to this module: drop what the rules no longer include
            for stale in [f for f in folder.glob("*.parquet") if f.stem not in keep]:
                stale.unlink()

    # --- names for NSE main-board stocks too, so a search by company name works everywhere
    main = listings[~listings["sme"]]
    for r in main.itertuples():
        names.append({"symbol": r.symbol, "exchange": "NSEEQ", "name": r.name, "isin": r.isin, "series": r.series})

    pd.DataFrame(names).drop_duplicates(["symbol", "exchange"]).to_csv(data_root / "instruments_names.csv", index=False)
    (daily / "source.json").write_text(
        json.dumps(
            {"source": "NSE_BSE_PUBLIC_EOD", "exchanges": ["BSEEQ", "NSESME", "NSEEQ"]},
            indent=2,
        ),
        encoding="utf8",
    )
    logger.info("market history rebuilt: {}", counts)
    return counts


def sync(
    data_root: Path,
    *,
    request_timeout_seconds: float,
    start: date,
    workers: int,
    end: date | None = None,
    force_rebuild: bool = False,
) -> dict[str, Any]:
    """Download any missing sessions, then rebuild the caches if anything changed."""
    data_root = Path(data_root)
    end = end or datetime.now().date()
    days = [start + timedelta(i) for i in range((end - start).days + 1)]
    bse_new = _download_days(
        data_root / "bse_bhav",
        _fetch_bse,
        days,
        workers,
        request_timeout_seconds,
    )
    nse_new = _download_days(
        data_root / "nse_bhav",
        _fetch_nse,
        days,
        workers,
        request_timeout_seconds,
    )
    if nse_new:
        _prune_nse_duplicates(data_root / "nse_bhav")
    counts = (
        rebuild(data_root, request_timeout_seconds=request_timeout_seconds)
        if (bse_new or nse_new or force_rebuild)
        else {}
    )
    return {"bse_days": bse_new, "nse_days": nse_new, "built": counts, "source": "NSE_BSE_PUBLIC_EOD"}


def due(data_root: Path, now: datetime | None = None) -> bool:
    """Once per weekday after the close (exchange files land by ~17:30 IST), and a catch-up after downtime."""
    now = now or datetime.now()
    marker = Path(data_root) / "market_history.json"
    try:
        last = datetime.fromisoformat(marker.read_text(encoding="utf8").strip()).date()
    except (OSError, ValueError):
        return True
    if last == now.date():
        return False
    return (now.date() - last).days >= 3 or (now.weekday() < 5 and now.hour >= 18)


def mark_done(data_root: Path, now: datetime | None = None) -> None:
    (Path(data_root) / "market_history.json").write_text((now or datetime.now()).isoformat(), encoding="utf8")


__all__ = ["BSE_EXCHANGE", "SME_EXCHANGE", "due", "mark_done", "rebuild", "sync"]
