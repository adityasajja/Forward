"""Wire the shared IIFL instrument catalog and its application service."""

from __future__ import annotations

from forward.brokers.iifl.instrument_catalog import build_iifl_instrument_catalog
from forward.config.settings import get_settings
from forward.core.broker_catalog_ports import BrokerInstrumentCatalogPort
from forward.core.order_ports import OrderInstrumentLookupPort
from forward.services.instrument_catalog import InstrumentCatalogService

INSTRUMENT_CATALOG: BrokerInstrumentCatalogPort = build_iifl_instrument_catalog(
    get_settings().iifl_contract_cache_ttl_seconds
)
_INSTRUMENT_CATALOG_SERVICE = InstrumentCatalogService(INSTRUMENT_CATALOG)


def get_instrument_catalog_service() -> InstrumentCatalogService:
    """Return the configured broker instrument catalog use case."""
    return _INSTRUMENT_CATALOG_SERVICE


def get_order_instrument_lookup() -> OrderInstrumentLookupPort:
    """Return the shared exact-contract lookup used for order risk checks."""
    return INSTRUMENT_CATALOG.instrument
