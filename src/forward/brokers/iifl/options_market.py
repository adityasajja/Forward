"""IIFL adapter for option contract and session data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from forward.brokers.iifl.auth import Session, SessionStore
from forward.brokers.iifl.client import IiflClient
from forward.brokers.iifl.contracts import InstrumentMaster
from forward.brokers.iifl.feeds import bridge_topic
from forward.config.settings import Settings
from forward.core.enums import AssetClass
from forward.core.instrument_models import Instrument
from forward.core.option_catalog import OptionContractCatalog


class IiflOptionsMarketAdapter:
    """Load option-market inputs from IIFL files and services."""

    def __init__(
        self,
        settings: Settings,
        *,
        index_exchange: str,
        options_exchange: str,
    ) -> None:
        """Create the adapter with application settings.

        Args:
            settings: IIFL connection and option-contract file settings.
            index_exchange: Configured segment used to load index contracts.
            options_exchange: Configured segment used to load option contracts.
        """
        self._settings = settings
        self._index_exchange = index_exchange
        self._options_exchange = options_exchange

    def session(self) -> Session | None:
        """Load the signed-in IIFL session from its configured store."""
        return SessionStore(self._settings.iifl_session_cache).load()

    def forward_catalog(self, underlying: str, exchange: str) -> OptionContractCatalog:
        """Build the option catalog for an underlying from IIFL contracts."""
        contracts = InstrumentMaster().options_for_underlying(underlying, exchange)
        if not contracts:
            raise LookupError(f"no cached option contracts found for {exchange}")
        return OptionContractCatalog(contracts)

    def spot_instrument(self, symbol: str, exchange: str) -> Instrument:
        """Resolve the spot instrument from the configured exchange catalog."""
        if exchange.upper() == self._index_exchange.upper():
            path = Path(self._settings.options_index_contracts_path)
            rows = json.loads(path.read_text(encoding="utf-8"))
            match = next(
                (
                    row
                    for row in rows
                    if str(row.get("underlyingInstrumentSymbol", "")).upper() == symbol.upper()
                ),
                None,
            )
            if match is None:
                raise LookupError(f"index {symbol!r} is not in the configured index contract file")
            return Instrument(
                symbol=symbol,
                asset_class=AssetClass.INDEX,
                exchange=self._index_exchange,
                conid=int(match["instrumentId"]),
            )
        matches = [
            item
            for item in InstrumentMaster().instruments([exchange])
            if item.symbol.upper() == symbol.upper()
        ]
        if len(matches) != 1:
            raise LookupError(f"expected one {exchange} contract for {symbol}, found {len(matches)}")
        return matches[0]

    def bridge_topic(self, instrument: Instrument) -> str:
        """Return the IIFL bridge topic for an instrument."""
        return bridge_topic(instrument)

    def refresh_contract_master(self) -> dict[str, Any]:
        """Refresh IIFL's NSE F&O contract file and summarize option coverage."""
        store = SessionStore(self._settings.iifl_session_cache)
        session = store.load()
        if session is None:
            raise LookupError("Authenticate with IIFL before refreshing the options contract master.")
        client = IiflClient(
            app_key=self._settings.iifl_app_key,
            app_secret=self._settings.iifl_app_secret,
            base_url=self._settings.iifl_base_url,
            timeout=self._settings.iifl_timeout,
            session_store=store,
            force_ipv4=self._settings.iifl_force_ipv4,
        )
        client.set_session(session)
        try:
            master = InstrumentMaster(client)
            exchange = self._options_exchange
            frame = master.sync([exchange], force=True)
            option_rows = frame.loc[frame["asset_class"] == "OPTION"]
            if option_rows.empty:
                raise RuntimeError(f"IIFL returned no option contracts in the {exchange} contract master.")
            underlyings = sorted(
                value
                for value in option_rows["underlying"].dropna().astype(str).str.upper().unique()
                if value and value not in {"NAN", "NONE"}
            )
            return {
                "exchange": exchange,
                "contracts": len(option_rows),
                "underlyings": underlyings,
                "refreshed": True,
            }
        finally:
            client.close()

    def contract_master_status(self) -> dict[str, Any]:
        """Report cached NSE F&O contract coverage without a network request."""
        exchange = self._options_exchange
        summary = InstrumentMaster().cached_summary([exchange])
        option_count = int(summary["option_count"])
        return {
            "available": option_count > 0,
            "contracts": option_count,
            "underlyings": summary["underlyings"],
        }
