"""Verify runner clock behavior in Indian market time."""

from __future__ import annotations

from datetime import datetime

from forward.appdb.order_storage import SqlOrderStorage
from forward.appdb.paper_ledger_storage import SqlPaperLedgerStorage
from forward.appdb.paper_runner_storage import SqlPaperRunnerStorage
from forward.appdb.portfolio_storage import SqlPortfolioStorage
from forward.appdb.risk_state_storage import SqlRiskStateStorage
from forward.appdb.system_state_storage import SqlSystemStateStorage
from forward.appdb.trade_journal_storage import SqlTradeJournalStorage
from forward.config.settings import get_settings
from forward.data.cached_market_frames import CachedMarketFramesAdapter
from forward.data.vwap_liquidity import NseBhavcopyLiquidityAdapter
from forward.market_calendar import IST, get_market_calendar
from forward.services.paper_ledger import PaperLedger
from forward.services.risk import RiskStateService
from tests.market_data_fakes import NoopSignalContextEnricher
from tests.order_fakes import order_instrument_lookup
from tests.paper_price_fakes import StaticRunnerVenueProvider, no_live_price


def test_the_runner_asks_the_calendar_in_indian_time_not_utc():
    from forward.services import runner

    cal = get_market_calendar()
    now = runner._now_ist()
    assert now.tzinfo is not None and now.utcoffset().total_seconds() == 5.5 * 3600
    # 10:00 on a Monday in India is open, however the machine clock is set
    assert cal.is_market_open(datetime(2026, 9, 21, 10, 0, tzinfo=IST))


def test_a_pass_without_a_time_uses_the_indian_clock(app_db, monkeypatch):
    from forward.services import runner as runner_module

    monday_ten = datetime(2026, 9, 21, 10, 0, tzinfo=IST)
    monkeypatch.setattr(runner_module, "_now_ist", lambda: monday_ten)

    tick = runner_module.PaperRunner(
        interval_seconds=get_settings().runner_interval_seconds,
        lookback_days=get_settings().runner_lookback_days,
        minimum_sleep_seconds=get_settings().runner_min_sleep_seconds,
        market_wait_seconds=get_settings().runner_market_wait_seconds,
        symbol_retry_seconds=get_settings().deployment_symbol_retry_seconds,
        signal_context=NoopSignalContextEnricher(),
        daily_history=CachedMarketFramesAdapter(),
        vwap_liquidity=NseBhavcopyLiquidityAdapter(get_settings().research_data_directory),
        sector_table={},
        instrument_lookup=order_instrument_lookup,
        journal_storage=SqlTradeJournalStorage(app_db),
        portfolio_storage=SqlPortfolioStorage(app_db),
        ledger=PaperLedger(storage=SqlPaperLedgerStorage(app_db), price_source=no_live_price),
        order_storage=SqlOrderStorage(app_db),
        runner_storage=SqlPaperRunnerStorage(app_db),
        state_storage=SqlSystemStateStorage(app_db),
        risk_state=RiskStateService(SqlRiskStateStorage(app_db), get_settings()),
        calendar=get_market_calendar(),
        venue_provider=StaticRunnerVenueProvider(
            prices=no_live_price,
            instruments=order_instrument_lookup,
        ),
    ).pass_once()

    assert tick.at == monday_ten
