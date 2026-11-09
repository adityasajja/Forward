"""Instrument master (contract files).

The IIFL contract files are downloaded per segment, e.g.
``GET /v1/contractfiles/NSEFO.json``. Field naming differs slightly across
segments/files, so :func:`normalize_contract` tolerates the common spellings
rather than assuming one schema. Run ``forward instruments sync`` and then eyeball
the output once against your own file — better to fail loudly at load time
than to trade the wrong contract.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from loguru import logger

from forward.core.enums import AssetClass, OptionExpiryType, OptionType
from forward.core.instrument_models import Instrument

CACHE_DIR = Path(".cache/contracts")


def _missing(value) -> bool:
    """True for None, empty string, NaN and NaT.

    Contract files leave absent fields empty, and pandas round-trips missing
    values back as NaN/NaT. The naive ``value not in (None, "")`` check misses
    both, because ``nan not in (None, "")`` is True — which silently turned
    absent strikes into NaN and absent underlyings into the string "NAN".
    """
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _pick(row: dict, *keys: str, default=None):
    for key in keys:
        if key in row and not _missing(row[key]):
            return row[key]
    return default


def _underlying_symbol(row: dict) -> str:
    """Return the normalized underlying symbol from a raw contract row."""
    value = _pick(
        row,
        "underlyingInstrumentSymbol",
        "UnderlyingInstrumentSymbol",
        "underlyingSymbol",
        "UnderlyingSymbol",
        "underlyingInstrumentName",
        "UnderlyingInstrumentName",
        "underlying",
        "Underlying",
    )
    return str(value).strip().upper() if value is not None else ""


def _parse_expiry(value) -> date | None:
    if _missing(value) or value == "NA" or value == 0:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).replace("T00:00:00", "").strip()
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d-%m-%Y", "%Y%m%d", "%d %b %Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognized IIFL contract expiry: {value!r}")


def _asset_class(exchange: str, instrument_type: str | None) -> AssetClass:
    ex = exchange.upper()
    if ex.endswith("FO") or ex.endswith("COMM"):
        if not instrument_type:
            raise ValueError(f"IIFL derivative contract on {ex} has no instrument type")
        itype = instrument_type.upper()
        if "OPT" in itype or "CE" in itype or "PE" in itype:
            return AssetClass.OPTION
        if "FUT" in itype:
            return AssetClass.FUTURE
        raise ValueError(f"unknown IIFL derivative instrument type: {instrument_type!r}")
    if ex.endswith("CURR"):
        return AssetClass.FUTURE
    if ex == "INDICES":
        return AssetClass.INDEX
    return AssetClass.EQUITY


def normalize_contract(row: dict, exchange: str) -> Instrument:
    """Map one raw contract-file row onto our domain :class:`Instrument`."""
    ex = exchange.upper()
    instrument_id = _pick(row, "instrumentId", "InstrumentId", "ExchangeInstrumentId",
                          "exchangeInstrumentId", "id", "ID")
    if instrument_id is None:
        raise ValueError(f"contract row has no instrument id: {row}")

    trading_symbol_value = _pick(
        row, "tradingSymbol", "TradingSymbol", "symbol", "Symbol"
    )
    if trading_symbol_value is None:
        raise ValueError(f"contract row has no trading symbol: {row}")
    trading_symbol = str(trading_symbol_value)
    description = _pick(row, "name", "Name", "description", "Description", "companyName")
    instrument_type = _pick(row, "instrumentType", "InstrumentType", "series", "Series",
                            "instrumentName")
    lot_size_raw = _pick(row, "lotSize", "LotSize", "marketLot", "MarketLot", "boardLot")
    tick_size_raw = _pick(row, "tickSize", "TickSize", "tick")
    multiplier_raw = _pick(
        row, "multiplier", "Multiplier", "contractMultiplier", "contractSize"
    )
    is_derivative = ex.endswith(("FO", "COMM", "CURR"))
    if is_derivative and lot_size_raw is None:
        raise ValueError(f"derivative contract {trading_symbol} has no lot size")
    if tick_size_raw is None:
        raise ValueError(f"contract {trading_symbol} has no tick size")
    if is_derivative and multiplier_raw is None:
        raise ValueError(f"derivative contract {trading_symbol} has no multiplier")
    lot_size = float(lot_size_raw) if lot_size_raw is not None else 1.0
    tick_size = float(tick_size_raw)
    multiplier = float(multiplier_raw) if multiplier_raw is not None else 1.0
    if lot_size <= 0 or tick_size <= 0 or multiplier <= 0:
        raise ValueError(f"contract {trading_symbol} has non-positive trading metadata")
    strike = _pick(row, "strike", "StrikePrice", "strikePrice")
    strike_value: float | None = None
    if not _missing(strike):
        try:
            parsed = float(strike)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ValueError(f"contract {trading_symbol} has an invalid strike") from exc
        if not pd.notna(parsed):
            raise ValueError(f"contract {trading_symbol} has an invalid strike")
        strike_value = parsed
    option_type_raw = _pick(row, "optionType", "OptionType", "option_type")
    expiry_type_raw = _pick(row, "expiryType", "ExpiryType", "expiry_type")

    asset_class = _asset_class(ex, instrument_type)
    underlying_text = _underlying_symbol(row)
    expiry = _parse_expiry(_pick(row, "expiry", "ExpiryDate", "expiryDate", "Expiry"))
    if is_derivative and expiry is None:
        raise ValueError(f"derivative contract {trading_symbol} has no expiry")
    if is_derivative and not underlying_text:
        raise ValueError(f"derivative contract {trading_symbol} has no underlying")
    option_type = None
    if option_type_raw:
        text = str(option_type_raw).upper()
        if text.startswith("C"):
            option_type = OptionType.CALL
        elif text.startswith("P"):
            option_type = OptionType.PUT
        else:
            raise ValueError(f"contract {trading_symbol} has an invalid option type")
    if asset_class is AssetClass.OPTION and (strike_value is None or option_type is None):
        raise ValueError(f"option contract {trading_symbol} has no strike or option type")
    expiry_type = None
    if expiry_type_raw is not None:
        try:
            expiry_type = OptionExpiryType(str(expiry_type_raw).strip().upper())
        except ValueError as exc:
            raise ValueError(
                f"option contract {trading_symbol} has an unsupported expiry type: {expiry_type_raw!r}"
            ) from exc
    if asset_class is AssetClass.OPTION and expiry_type is None:
        raise ValueError(
            f"option contract {trading_symbol} is missing explicit weekly/monthly expiry metadata"
        )

    return Instrument(
        symbol=trading_symbol.strip().upper(),
        asset_class=asset_class,
        currency="INR",
        exchange=ex,
        multiplier=multiplier,
        tick_size=tick_size,
        min_quantity=lot_size if asset_class in (AssetClass.FUTURE, AssetClass.OPTION) else 1.0,
        quantity_step=lot_size if asset_class in (AssetClass.FUTURE, AssetClass.OPTION) else 1.0,
        expiry=expiry,
        strike=strike_value,
        option_type=option_type,
        expiry_type=expiry_type,
        underlying=underlying_text or None,
        conid=int(instrument_id),
        local_symbol=str(description or trading_symbol)[:64],
        trading_class=str(instrument_type or "")[:32] or None,
    )


class InstrumentMaster:
    """Loads, caches, and queries the contract universe."""

    def __init__(self, client=None, cache_dir: Path | str = CACHE_DIR) -> None:
        self.client = client
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._frame: pd.DataFrame | None = None

    # ------------------------------------------------------------------
    def sync(self, exchanges: list[str], force: bool = False) -> pd.DataFrame:
        frames = []
        if not exchanges:
            raise ValueError("at least one exchange is required to sync contracts")
        for ex in exchanges:
            path = self.cache_dir / f"{ex.upper()}.json"
            downloaded = force or not path.exists()
            if downloaded:
                if self.client is None:
                    raise RuntimeError("no client provided and cache is cold")
                rows = self.client.contract_file(ex)
            else:
                rows = json.loads(path.read_text(encoding="utf8"))

            if not isinstance(rows, list):
                raise ValueError(f"IIFL contract file for {ex} must contain a row list")
            instruments = []
            for index, row in enumerate(rows):
                if not isinstance(row, dict):
                    raise ValueError(f"IIFL contract row {index} in {ex} is not an object")
                try:
                    instruments.append(normalize_contract(row, ex))
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"invalid IIFL contract row {index} in {ex}: {exc}") from exc
            if downloaded:
                path.write_text(json.dumps(rows), encoding="utf8")
                logger.info("downloaded {} contracts for {}", len(rows), ex)
            frames.append(pd.DataFrame([i.model_dump() for i in instruments]))

        self._frame = pd.concat(frames, ignore_index=True)
        logger.info("instrument master holds {} contracts", len(self._frame))
        return self._frame

    def load_cached(self, exchanges: list[str]) -> pd.DataFrame:
        if not exchanges:
            raise ValueError("at least one exchange is required to load contracts")
        frames = []
        missing = [
            exchange.upper()
            for exchange in exchanges
            if not (self.cache_dir / f"{exchange.upper()}.json").is_file()
        ]
        if missing:
            raise FileNotFoundError(
                f"contract cache missing for {', '.join(missing)}; sync contracts explicitly"
            )
        for ex in exchanges:
            path = self.cache_dir / f"{ex.upper()}.json"
            rows = json.loads(path.read_text(encoding="utf8"))
            if not isinstance(rows, list):
                raise ValueError(f"cached contract file {path} must contain a row list")
            instruments = []
            for index, row in enumerate(rows):
                if not isinstance(row, dict):
                    raise ValueError(f"cached contract row {index} in {path} is not an object")
                try:
                    instruments.append(normalize_contract(row, ex))
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"invalid cached contract row {index} in {path}: {exc}") from exc
            frames.append(pd.DataFrame([i.model_dump() for i in instruments]))
        self._frame = pd.concat(frames, ignore_index=True)
        logger.info("instrument master loaded {} contracts from cache", len(self._frame))
        return self._frame

    # ------------------------------------------------------------------
    @property
    def frame(self) -> pd.DataFrame:
        if self._frame is None:
            raise RuntimeError("instrument master not loaded — call sync() first")
        return self._frame

    def instruments(self, exchanges: list[str]) -> list[Instrument]:
        """Return cached contracts as validated Instruments.

        Args:
            exchanges: Segments whose contract files are already available.

        Raises:
            RuntimeError: If any requested contract file is not cached.
        """
        frame = self.load_cached(exchanges)
        result: list[Instrument] = []
        for record in frame.to_dict(orient="records"):
            cleaned = {key: (None if _missing(value) else value) for key, value in record.items()}
            result.append(Instrument.model_validate(cleaned))
        return result

    def options_for_underlying(self, underlying: str, exchange: str) -> list[Instrument]:
        """Load option contracts for one underlying without materializing all segments.

        Args:
            underlying: Normalized exchange symbol, such as ``NIFTY``.
            exchange: Cached derivative segment containing the contracts.

        Returns:
            Validated options contracts for the requested underlying.

        Raises:
            FileNotFoundError: If the exchange contract cache is missing.
        """
        normalized_underlying = underlying.strip().upper()
        normalized_exchange = exchange.strip().upper()
        path = self.cache_dir / f"{normalized_exchange}.json"
        if not path.exists():
            raise FileNotFoundError(f"contract cache is missing for {normalized_exchange}")

        rows = json.loads(path.read_text(encoding="utf8"))
        if not isinstance(rows, list):
            raise ValueError(f"cached contract file {path} must contain a row list")
        instruments: list[Instrument] = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise ValueError(f"cached contract row {index} in {path} is not an object")
            if _underlying_symbol(row) != normalized_underlying:
                continue
            instrument_type = _pick(
                row, "instrumentType", "InstrumentType", "series", "Series", "instrumentName"
            )
            if _asset_class(normalized_exchange, instrument_type) is not AssetClass.OPTION:
                continue
            try:
                instruments.append(normalize_contract(row, normalized_exchange))
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"invalid {normalized_underlying} option contract row {index}: {exc}"
                ) from exc
        return instruments

    def cached_summary(self, exchanges: list[str]) -> dict[str, object]:
        """Summarize cached option coverage without building thousands of models.

        Args:
            exchanges: Cached segments to summarize.

        Returns:
            Option contract count and sorted underlying names.
        """
        option_count = 0
        underlyings: set[str] = set()
        for exchange in exchanges:
            normalized_exchange = exchange.strip().upper()
            path = self.cache_dir / f"{normalized_exchange}.json"
            if not path.exists():
                continue
            rows = json.loads(path.read_text(encoding="utf8"))
            for row in rows:
                instrument_type = _pick(
                    row, "instrumentType", "InstrumentType", "series", "Series", "instrumentName"
                )
                if _asset_class(normalized_exchange, instrument_type) is not AssetClass.OPTION:
                    continue
                option_count += 1
                underlying = _underlying_symbol(row)
                if underlying:
                    underlyings.add(underlying)
        return {"option_count": option_count, "underlyings": sorted(underlyings)}

    def find(
        self,
        symbol: str,
        exchange: str | None = None,
        expiry: date | None = None,
        strike: float | None = None,
        option_type: OptionType | None = None,
    ) -> Instrument:
        df = self.frame
        mask = df["symbol"].str.upper() == symbol.upper()
        if exchange:
            mask &= df["exchange"].str.upper() == exchange.upper()
        if expiry:
            mask &= df["expiry"] == expiry
        if strike is not None:
            mask &= (df["strike"] - strike).abs() < 1e-9
        if option_type:
            mask &= df["option_type"] == option_type
        hits = df[mask]
        if hits.empty:
            raise KeyError(f"no contract matching symbol={symbol} exchange={exchange}")
        # Going back through the DataFrame converts absent values to NaN/NaT,
        # and pydantic rejects a float for the OptionType enum.
        row = {k: (None if _missing(v) else v) for k, v in hits.iloc[0].to_dict().items()}
        return Instrument(**row)

    def contract_id(self, symbol: str, exchange: str) -> str:
        """Return the broker ID for one exact trading symbol.

        Args:
            symbol: Exact trading symbol in the selected exchange segment.
            exchange: Exchange segment containing the instrument.

        Returns:
            Broker instrument ID as text for IIFL request payloads.

        Raises:
            KeyError: If the exact symbol is not present in the master.
            ValueError: If the listed instrument has no broker ID.
        """
        instrument = self.find(symbol, exchange)
        if instrument.conid is None:
            raise ValueError(
                f"contract has no broker instrument ID: {symbol} on {exchange}"
            )
        return str(instrument.conid)

    def search(self, text: str, exchange: str | None = None, limit: int = 25) -> pd.DataFrame:
        df = self.frame
        mask = df["symbol"].str.contains(text.upper(), na=False)
        if exchange:
            mask &= df["exchange"].str.upper() == exchange.upper()
        return df[mask].head(limit)

    def nearest_expiry(self, symbol: str, exchange: str, after: date | None = None) -> date:
        after = after or date.today()
        df = self.frame
        mask = (
            (df["symbol"].str.upper() == symbol.upper())
            & (df["exchange"].str.upper() == exchange.upper())
            & (df["expiry"].notna())
        )
        expiries = sorted(e for e in df[mask]["expiry"].dropna().unique() if e >= after)
        if not expiries:
            raise KeyError(f"no expiry for {symbol} on {exchange} after {after}")
        return expiries[0]
