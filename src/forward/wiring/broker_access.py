"""Build broker access services with the configured IIFL adapter."""

from __future__ import annotations

from typing import Any

from forward.brokers.iifl.access import IiflBrokerAccessAdapter
from forward.config.settings import get_settings
from forward.core.broker_access_ports import AuthenticatedBrokerClientPort
from forward.services.broker_access import BrokerAccessService

_BROKER_ACCESS: BrokerAccessService | None = None


def get_broker_access() -> BrokerAccessService:
    """Return the broker access service for the current settings."""
    global _BROKER_ACCESS
    if _BROKER_ACCESS is None:
        settings = get_settings()
        _BROKER_ACCESS = BrokerAccessService(
            provider=IiflBrokerAccessAdapter(settings),
            environment=settings.env,
        )
    return _BROKER_ACCESS


def reset_broker_access() -> None:
    """Clear broker clients and settings for a new application context."""
    global _BROKER_ACCESS
    _BROKER_ACCESS = None


def get_authenticated_broker_client() -> AuthenticatedBrokerClientPort:
    """Return the current worker's authenticated broker client."""
    return get_broker_access().authenticated_client()


def get_live_broker() -> Any:
    """Return an authenticated broker that may place orders."""
    return get_broker_access().live_broker()


def get_read_broker() -> Any:
    """Return an authenticated broker for read-only account access."""
    return get_broker_access().read_broker()
