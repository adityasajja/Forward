"""Wire paper-account services and storage to the application database."""

from __future__ import annotations

from forward.appdb.engine import get_app_db
from forward.appdb.order_storage import SqlOrderStorage
from forward.appdb.paper_ledger_storage import SqlPaperLedgerStorage
from forward.appdb.paper_runner_storage import SqlPaperRunnerStorage
from forward.appdb.pnl_calendar_storage import SqlPaperPnlStorage
from forward.appdb.system_state_storage import SqlSystemStateStorage
from forward.core.paper_models import PaperPriceSourcePort
from forward.services.paper_ledger import PaperLedger

_PAPER_RUNNER_STORAGE = SqlPaperRunnerStorage(get_app_db())
_PAPER_ORDER_STORAGE = SqlOrderStorage(get_app_db())
_PAPER_STATE_STORAGE = SqlSystemStateStorage(get_app_db())
_PAPER_PNL_STORAGE = SqlPaperPnlStorage(get_app_db())


def get_paper_ledger() -> PaperLedger:
    """Build the ledger for paper account reads and fills.

    Returns:
        The ledger wired to the application database and live tick source.
    """
    return PaperLedger(
        storage=SqlPaperLedgerStorage(get_app_db()),
        price_source=get_live_paper_price_source(),
    )


def get_paper_runner_storage() -> SqlPaperRunnerStorage:
    """Return the shared storage adapter for runner deployments and strategies."""
    return _PAPER_RUNNER_STORAGE


def get_paper_order_storage() -> SqlOrderStorage:
    """Return the shared order storage adapter used by paper execution."""
    return _PAPER_ORDER_STORAGE


def get_paper_state_storage() -> SqlSystemStateStorage:
    """Return the shared durable state adapter used by the paper runner."""
    return _PAPER_STATE_STORAGE


def get_paper_pnl_storage() -> SqlPaperPnlStorage:
    """Return storage for closed paper trades shown in the P&L calendar."""
    return _PAPER_PNL_STORAGE


def get_live_paper_price_source() -> PaperPriceSourcePort:
    """Build the paper execution price source from the shared live stream."""
    from forward.api.stream import get_broadcaster
    from forward.services.paper_prices import live_tick_source

    return live_tick_source(get_broadcaster())
