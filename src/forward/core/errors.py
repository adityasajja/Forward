"""Domain errors shared across application ports and adapters."""


class DuplicateWatchlistName(Exception):
    """A user already has a watchlist with the requested name."""


class InvalidWatchlistOrder(ValueError):
    """A watchlist order must contain each current symbol exactly once."""


class DuplicateScreenerName(Exception):
    """A user already has a saved screen with the requested name."""

    def __init__(self, name: str) -> None:
        """Create the error with the name that conflicted.

        Args:
            name: Requested screen name.
        """
        super().__init__(name)


class DuplicateAuthIdentity(Exception):
    """An account email or username is already in use."""


class UnknownScreenerUniverse(ValueError):
    """A screener requested a universe absent from the instrument catalog."""

    def __init__(self, name: str, known: list[str]) -> None:
        """Create the error with the requested and available names.

        Args:
            name: Requested universe name.
            known: Names available in the catalog.
        """
        self.name = name
        self.known = known
        names = ", ".join(known) or "none"
        super().__init__(f"unknown universe {name!r}. Available: all, {names}")


class ScreenerError(Exception):
    """A screener workflow failed and carries its HTTP response details.

    Attributes:
        code: Stable error code for API clients.
        status: HTTP status to return for the failure.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "screener_error",
        status: int = 400,
    ) -> None:
        """Create a screener error.

        Args:
            message: Human-readable reason for the failure.
            code: Stable code for API clients.
            status: HTTP status assigned by the API adapter.
        """
        self.code = code
        self.status = status
        super().__init__(message)


class DuplicateDefinition(ValueError):
    """A strategy version with this definition already exists.

    Args:
        existing_version: Version that already stores the definition.
    """

    def __init__(self, existing_version: int) -> None:
        self.existing_version = int(existing_version)
        super().__init__(f"an identical definition already exists as version {existing_version}")


class ContractCatalogUnavailable(RuntimeError):
    """The configured broker contract catalog could not be loaded."""


class MarketHistoryUnavailable(RuntimeError):
    """No cached bars are available for a requested market scan."""


class InvalidMarketIntelSort(ValueError):
    """A market-intelligence query requested an unsupported sort field."""


class InvalidAnalyticsFilter(ValueError):
    """An analytics query used a source or evidence grade we do not support."""


class AnalyticsBookTooLarge(ValueError):
    """An analytics request tried to aggregate more rows than one call allows."""


class BrokerUnavailable(RuntimeError):
    """A broker operation cannot run without an active allowed session."""

    def __init__(self, message: str, *, status: int, code: str) -> None:
        self.status = status
        self.code = code
        super().__init__(message)


class VenueError(RuntimeError):
    """A venue call failed and the order result is unknown.

    Args:
        message: Detail about the failed venue call.
        order_id: Order whose exchange result is unknown.
    """

    def __init__(self, message: str, *, order_id: str) -> None:
        self.order_id = order_id
        super().__init__(message)
