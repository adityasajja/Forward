"""Test fixtures for order use cases."""

from forward.core.instrument_models import Instrument


def order_instrument_lookup(symbol: str, exchange: str) -> Instrument:
    """Build a minimal listed instrument for isolated order tests.

    Args:
        symbol: Symbol requested by the order.
        exchange: Exchange segment requested by the order.

    Returns:
        A domain instrument with its standard unit multiplier.
    """
    return Instrument(symbol=symbol, exchange=exchange)
