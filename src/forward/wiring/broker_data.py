"""Wire broker session and account-data services to IIFL adapters."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from forward.brokers.iifl.account_read import IiflAccountReadAdapter
from forward.brokers.iifl.session import IiflSessionAdapter
from forward.brokers.iifl.tradebook import IiflTradebookReadAdapter
from forward.config.settings import get_settings
from forward.core.broker_account_ports import BrokerPositionReadPort
from forward.data.daily_tradebook import JsonDailyTradebookAdapter
from forward.services.broker_account_read import BrokerAccountReadService
from forward.services.broker_positions import BrokerPositionsService
from forward.services.broker_session import BrokerSessionService
from forward.services.read_coalescer import ReadCoalescer
from forward.services.tradebook import TradebookCaptureService

_BROKER_ACCOUNT_READS = ReadCoalescer()
_SETTINGS = get_settings()
_BROKER_SESSION_SERVICE = BrokerSessionService(
    IiflSessionAdapter(
        app_key=_SETTINGS.iifl_app_key,
        app_secret=_SETTINGS.iifl_app_secret,
        base_url=_SETTINGS.iifl_base_url,
        session_path=_SETTINGS.iifl_session_cache,
        redirect_url=_SETTINGS.iifl_redirect_url,
        timeout=_SETTINGS.iifl_timeout,
        force_ipv4=_SETTINGS.iifl_force_ipv4,
    )
)


def get_broker_session_service() -> BrokerSessionService:
    """Return the configured broker-session use case."""
    return _BROKER_SESSION_SERVICE


def build_broker_account_read_service(client: Any) -> BrokerAccountReadService:
    """Build account reads for one authenticated broker client.

    Args:
        client: Authenticated IIFL client for the current request.

    Returns:
        Account read service using the shared in-flight read coalescer.
    """
    return BrokerAccountReadService(
        IiflAccountReadAdapter(client),
        _BROKER_ACCOUNT_READS,
    )


def build_tradebook_capture_service(client: Any, data_root: Path) -> TradebookCaptureService:
    """Build a tradebook capture use case for one broker session.

    Args:
        client: Authenticated IIFL client.
        data_root: Directory that contains portfolio data.

    Returns:
        Service that saves raw daily trade and position rows.
    """
    return TradebookCaptureService(
        IiflTradebookReadAdapter(client),
        JsonDailyTradebookAdapter(data_root),
    )


def build_broker_positions_service(
    broker: BrokerPositionReadPort,
) -> BrokerPositionsService:
    """Build the normalized position query for one broker session.

    Args:
        broker: Broker that supplies normalized position records.

    Returns:
        A position query service bound to that broker.
    """
    return BrokerPositionsService(broker)
