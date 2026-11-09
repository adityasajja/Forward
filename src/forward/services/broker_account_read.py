"""Read broker account sections for the dashboard."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from forward.core.broker_account_ports import BrokerAccountReadPort
from forward.core.market_models import BrokerReadResult
from forward.core.read_coalescer_ports import ReadCoalescerPort

_PORTFOLIO_SECTIONS = ("limits", "positions", "holdings", "orders", "trades")


def parse_account_sections(requested: str | None) -> list[str]:
    """Validate section names and keep their requested order.

    Args:
        requested: Comma-separated section names, or ``None`` for all sections.

    Returns:
        Unique section names in request order.

    Raises:
        ValueError: If a section is unknown or no section was requested.
    """
    wanted = (
        [section.strip() for section in requested.split(",") if section.strip()]
        if requested
        else list(_PORTFOLIO_SECTIONS)
    )
    unknown = [section for section in wanted if section not in _PORTFOLIO_SECTIONS]
    if unknown:
        raise ValueError(f"unknown section(s): {unknown}")
    wanted = list(dict.fromkeys(wanted))
    if not wanted:
        raise ValueError("at least one portfolio section is required")
    return wanted


class BrokerAccountReadService:
    """Load requested account sections concurrently and shape dashboard results."""

    def __init__(
        self,
        account: BrokerAccountReadPort,
        reads: ReadCoalescerPort,
    ) -> None:
        """Create the account read service.

        Args:
            account: Adapter for read-only broker account data.
            reads: Shared coalescer for overlapping requests.
        """
        self._account = account
        self._reads = reads

    def sections(self, requested: str | None = None) -> dict[str, Any]:
        """Read the selected account sections.

        Args:
            requested: Comma-separated section names, or ``None`` for all sections.

        Returns:
            Requested sections in the order supplied by the caller.

        Raises:
            ValueError: If a section is unknown or no section was requested.
        """
        wanted = parse_account_sections(requested)
        with ThreadPoolExecutor(max_workers=len(wanted)) as pool:
            sections = dict(pool.map(self._shared_section, wanted))
        return sections

    def read(
        self, name: str, *, include_quotes: bool = True
    ) -> BrokerReadResult:
        """Read one broker section and share an identical in-flight request.

        Args:
            name: Account section to read.
            include_quotes: Whether holdings should include current quotes.

        Returns:
            Normalized broker records and any broker error message.

        Raises:
            ValueError: If the section name is unknown.
            Exception: If the broker request itself fails.
        """
        if name not in _PORTFOLIO_SECTIONS:
            raise ValueError(f"unknown section: {name}")
        if name == "holdings" and not include_quotes:
            return self._account.section(name, include_quotes=False)
        key = (self._account.session_identity, name)
        return self._reads.run(key, lambda: self._account.section(name))

    def _shared_section(self, name: str) -> tuple[str, dict[str, Any]]:
        """Read one section, sharing it with concurrent requests for this session."""
        try:
            result = self.read(name)
            if result.error:
                return name, {"rows": [], "count": 0, "error": result.error}
            return name, {"rows": result.rows, "count": len(result.rows)}
        except Exception as exc:  # noqa: BLE001 - each section reports its own error
            return name, {"rows": [], "count": 0, "error": str(exc)[:300]}
