"""Build paper venues for tests with explicit configured execution policies."""

from typing import Any

from forward.brokers.paper_venue import PaperVenue as _PaperVenue
from forward.config.settings import get_settings


def configured_paper_venue(**kwargs: Any) -> _PaperVenue:
    """Build a paper venue with configured quote age and no test liquidity data.

    Args:
        **kwargs: Explicit venue parameters supplied by the test.

    Returns:
        A paper venue for use with test doubles.
    """
    kwargs.setdefault("max_tick_age_seconds", get_settings().market_quote_max_age_seconds)
    kwargs.setdefault("adv", lambda _symbol, _exchange: None)
    return _PaperVenue(**kwargs)
