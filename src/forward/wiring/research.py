"""Wire research workflows to market history and validation storage."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from forward.config.settings import get_settings, research_data_root
from forward.core.backtest_ports import BacktestFeedPort
from forward.core.market_universe import DEFAULT_SCAN_UNIVERSE
from forward.data.research_history import CachedResearchHistoryAdapter
from forward.data.validation_reports import JsonValidationReportAdapter
from forward.services.local_scans import LocalScanService
from forward.services.market_scanning import MarketScanningService
from forward.services.research_catalog import ResearchCatalogService
from forward.services.research_history import ResearchHistoryService
from forward.services.research_validation import ResearchValidationService
from forward.services.validation_reports import ValidationReportService

_VALIDATION_REPORT_SERVICE = ValidationReportService(
    JsonValidationReportAdapter(research_data_root())
)
_RESEARCH_HISTORY_SERVICE = ResearchHistoryService(
    CachedResearchHistoryAdapter(), default_symbols=list(DEFAULT_SCAN_UNIVERSE)
)
_RESEARCH_CATALOG_SERVICE: ResearchCatalogService | None = None
_RESEARCH_VALIDATION_SERVICE: ResearchValidationService | None = None
_LOCAL_SCAN_SERVICE: LocalScanService | None = None


def get_local_scan_service() -> LocalScanService:
    """Return the shared use case for scans over cached market bars."""
    global _LOCAL_SCAN_SERVICE
    if _LOCAL_SCAN_SERVICE is None:
        from forward.core.market_universe import DEFAULT_SCAN_UNIVERSE
        from forward.wiring.market_data import CACHED_MARKET_FRAMES

        _LOCAL_SCAN_SERVICE = LocalScanService(
            CACHED_MARKET_FRAMES,
            cache_seconds=get_settings().market_scan_cache_seconds,
            default_symbols=list(DEFAULT_SCAN_UNIVERSE),
        )
    return _LOCAL_SCAN_SERVICE


def build_market_scanning_service(client: Any) -> MarketScanningService:
    """Create a market scanner for one authenticated client.

    Args:
        client: Authenticated IIFL client for the current request.

    Returns:
        Market scanner wired to the IIFL market adapter.
    """
    from forward.brokers.iifl.scanner_market import IiflScannerMarketAdapter

    return MarketScanningService(
        IiflScannerMarketAdapter(client),
        default_symbols=list(DEFAULT_SCAN_UNIVERSE),
        from_date=get_settings().scanner_start_date,
    )


def build_research_history_service(client: Any) -> ResearchHistoryService:
    """Create deep-history access for one authenticated research request.

    Args:
        client: Authenticated IIFL client for the current request.

    Returns:
        Research-history service wired to cached and IIFL adapters.
    """
    from forward.brokers.iifl.research_history import IiflResearchHistoryAdapter

    return ResearchHistoryService(
        CachedResearchHistoryAdapter(),
        IiflResearchHistoryAdapter(client),
        default_symbols=list(DEFAULT_SCAN_UNIVERSE),
    )


def build_synthetic_research_feed(symbols: tuple[str, ...]) -> BacktestFeedPort:
    """Create the synthetic feed used by walk-forward research.

    Args:
        symbols: Symbols that appear in the generated feed.

    Returns:
        Synthetic backtest feed for the configured research interval.
    """
    from forward.data.synthetic import SyntheticConfig, SyntheticFeed

    return SyntheticFeed(
        SyntheticConfig(
            symbols=symbols,
            start=datetime(2024, 1, 1, 9, 30),
            end=datetime(2024, 6, 28, 15, 59),
        )
    )


def get_research_history_service() -> ResearchHistoryService:
    """Return the shared service for cached market-history requests."""
    return _RESEARCH_HISTORY_SERVICE


def get_research_catalog_service() -> ResearchCatalogService:
    """Return the shared strategy catalog and its validation evidence."""
    global _RESEARCH_CATALOG_SERVICE
    if _RESEARCH_CATALOG_SERVICE is None:
        from forward.backtest.config import WARMUP_BARS
        from forward.strategy.strategies import STRATEGIES

        _RESEARCH_CATALOG_SERVICE = ResearchCatalogService(
            strategies=STRATEGIES,
            warmup_bars=WARMUP_BARS,
            validation_reports=get_validation_report_service(),
        )
    return _RESEARCH_CATALOG_SERVICE


def get_research_validation_service() -> ResearchValidationService:
    """Return the shared walk-forward validation service."""
    global _RESEARCH_VALIDATION_SERVICE
    if _RESEARCH_VALIDATION_SERVICE is None:
        _RESEARCH_VALIDATION_SERVICE = ResearchValidationService(get_research_catalog_service())
    return _RESEARCH_VALIDATION_SERVICE


def get_validation_report_service() -> ValidationReportService:
    """Return the service for stored validation and evidence reports."""
    return _VALIDATION_REPORT_SERVICE
