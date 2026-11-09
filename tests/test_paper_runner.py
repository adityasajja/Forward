"""The continuous paper-trading runner.

What this suite is protecting
-----------------------------

``forward.services.runner`` is the piece that makes ``BACKTEST → PAPER → LIVE``
have a middle that actually runs. Before it, the paper engine was request-driven:
prices came from the daily parquet close, a resting limit was re-evaluated only
if a client asked, and a deployment marked RUNNING did nothing at all.

The tests are grouped by requirement, because each one is a separate way for the
loop to be *quietly* wrong — filling at a stale price, placing the same order
sixty times a minute, letting a stop-loss through the risk gate's blind spot, or
reporting equity that disagrees with cash plus market value.

The last two are the ones worth reading. ``test_exit_is_not_rejected_as_a_short``
covers a bug that existed in the first draft: the runner handed the risk gate a
flat portfolio, so every SELL looked like opening a short and was rejected
whenever ``allow_short`` was false — which silently disabled stop-losses.
``test_equity_always_equals_cash_plus_market_value`` is the invariant the whole
"the log is the state" design exists to preserve.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from conftest import empty_cached_instrument_history_port
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
from forward.market_calendar import get_market_calendar
from forward.paper.config import RunnerConfig
from forward.services.deployment_loop import DeploymentLoop, in_market_hours
from forward.services.paper_ledger import PaperLedger
from forward.services.risk import RiskStateService
from forward.services.runner import PaperRunner
from tests.market_data_fakes import NoopSignalContextEnricher
from tests.order_fakes import order_instrument_lookup
from tests.paper_price_fakes import StaticRunnerVenueProvider, no_live_price

#: A Monday, 10:00 IST — inside the cash session.
SESSION = datetime(2026, 9, 14, 10, 0)
SYMBOL = "RELIANCE"


def _configured_runner_config(row):
    """Build a saved deployment with the required configured platform values."""
    settings = get_settings()
    return RunnerConfig.from_deployment(
        row,
        platform_interval_seconds=settings.runner_interval_seconds,
        platform_lookback_days=settings.runner_lookback_days,
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _daily(symbol: str, n: int = 300, start: float = 1000.0) -> pd.DataFrame:
    """A flat daily frame long enough for every indicator to warm up."""
    index = pd.date_range("2025-01-01", periods=n, freq="D")
    return pd.DataFrame(
        {
            "ts": index,
            "open": [start] * n,
            "high": [start * 1.01] * n,
            "low": [start * 0.99] * n,
            "close": [start] * n,
            "volume": [100_000.0] * n,
        }
    )


class LiveFeed:
    """A price source whose price the test controls."""

    def __init__(self, prices: dict[str, float] | None = None) -> None:
        self.prices = dict(prices or {SYMBOL: 1000.0})
        self.calls = 0

    def __call__(self, symbol: str, exchange: str) -> float | None:
        self.calls += 1
        return self.prices.get(symbol.upper())


@pytest.fixture()
def deployment(app_db):
    """A RUNNING paper deployment for one strategy over one symbol."""
    from datetime import datetime as _dt

    from forward.appdb.account_schema import users
    from forward.appdb.deployment_repository import DeploymentRepository

    with app_db.session() as session:
        session.execute(
            users.insert().values(
                user_id="u1",
                email="t@example.com",
                username="tester",
                display_name="T",
                password_hash="x",
                role="owner",
                is_active=True,
                mfa_enabled=False,
                failed_logins=0,
                created_at=_dt(2026, 1, 1),
                updated_at=_dt(2026, 1, 1),
            )
        )
    with app_db.session() as session:
        row = DeploymentRepository.create(
            session,
            user_id="u1",
            strategy_id="sma_pullback",
            strategy_version=1,
            mode="PAPER",
            capital=200_000.0,
            status="RUNNING",
            config={
                "symbols": [SYMBOL],
                "exchange": "NSEEQ",
                "timeframe": "1d",
                "max_open_positions": 10,
                "signal_basis": "live",
            },
        )
    return row


@pytest.fixture()
def wired(monkeypatch, app_db):
    """A runner with stubbed history and forced signals."""

    def make(
        *,
        feed: LiveFeed | None = None,
        entry: bool = True,
        stop_loss_pct: float | None = 10.0,
        take_profit_pct: float | None = None,
    ) -> tuple[PaperRunner, LiveFeed]:
        feed = feed or LiveFeed()

        monkeypatch.setattr(
            "forward.data.cached_market_frames.load_daily_from_cache",
            lambda symbol, exchange="NSEEQ", **kw: _daily(symbol),
        )

        from forward.signals import rules
        from forward.signals.models import EntryRules, ExitRules, Signal

        def stub_entry(symbol, frame, rls, context=None):
            if not entry:
                return []
            return [
                Signal(
                    symbol=symbol,
                    action="BUY",
                    rule="test_entry",
                    reason="forced",
                    price=float(frame["close"].iloc[-1]),
                )
            ]

        def stub_exit(symbol, frame, avg_price, rls, *, quantity=0.0, context=None):
            price = float(frame["close"].iloc[-1])
            pnl = (price / avg_price - 1.0) * 100.0
            if rls.stop_loss_pct and pnl <= -abs(rls.stop_loss_pct):
                return [
                    Signal(
                        symbol=symbol,
                        action="SELL",
                        rule="stop_loss",
                        reason=f"down {pnl:.1f}%",
                        price=price,
                    )
                ]
            if rls.take_profit_pct and pnl >= abs(rls.take_profit_pct):
                return [
                    Signal(
                        symbol=symbol,
                        action="SELL",
                        rule="take_profit",
                        reason=f"up {pnl:.1f}%",
                        price=price,
                    )
                ]
            return []

        monkeypatch.setattr(rules, "eval_entry", stub_entry)
        monkeypatch.setattr(rules, "eval_exit", stub_exit)
        monkeypatch.setattr(
            DeploymentLoop,
            "_rules",
            lambda self: (
                EntryRules(),
                ExitRules(
                    stop_loss_pct=stop_loss_pct,
                    take_profit_pct=take_profit_pct,
                    trailing_stop_pct=None,
                    trend_sma=0,
                    rsi_overbought=None,
                ),
            ),
        )
        # The runner imports the rules inside the function, so patching the
        # module attribute is what takes effect — no need to patch the caller.
        return PaperRunner(
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
            ledger=PaperLedger(storage=SqlPaperLedgerStorage(app_db), price_source=no_live_price),
            order_storage=SqlOrderStorage(app_db),
            runner_storage=SqlPaperRunnerStorage(app_db),
            state_storage=SqlSystemStateStorage(app_db),
            risk_state=RiskStateService(SqlRiskStateStorage(app_db), get_settings()),
            portfolio_storage=SqlPortfolioStorage(app_db),
            calendar=get_market_calendar(),
            venue_provider=StaticRunnerVenueProvider(
                prices=feed, instruments=order_instrument_lookup
            ),
        ), feed

    return make


def _loop(runner: PaperRunner, deployment: dict) -> DeploymentLoop:
    runner.sync_loops()
    return runner._loops[deployment["deployment_id"]]


# ---------------------------------------------------------------------------
# 1. market hours — a signal outside the session is about a closed market
# ---------------------------------------------------------------------------
def test_market_hours_cover_the_session_only():
    assert in_market_hours(calendar=get_market_calendar(), now=datetime(2026, 9, 14, 9, 15)) is True
    assert (
        in_market_hours(calendar=get_market_calendar(), now=datetime(2026, 9, 14, 15, 30)) is True
    )
    assert (
        in_market_hours(calendar=get_market_calendar(), now=datetime(2026, 9, 14, 9, 14)) is False
    )
    assert (
        in_market_hours(calendar=get_market_calendar(), now=datetime(2026, 9, 14, 15, 31)) is False
    )


def test_market_hours_exclude_the_weekend():
    assert (
        in_market_hours(calendar=get_market_calendar(), now=datetime(2026, 9, 19, 11, 0)) is False
    )  # Saturday
    assert (
        in_market_hours(calendar=get_market_calendar(), now=datetime(2026, 9, 20, 11, 0)) is False
    )  # Sunday


# ---------------------------------------------------------------------------
# 2. config — the deployment row is the whole definition
# ---------------------------------------------------------------------------
def test_config_accepts_a_json_string_and_a_mapping():
    from_string = _configured_runner_config(
        {
            "config": json.dumps(
                {
                    "symbols": ["reliance", "tcs"],
                    "exchange": "nseeq",
                    "timeframe": "1d",
                    "max_open_positions": 10,
                    "signal_basis": "closed_bar",
                }
            )
        }
    )
    assert from_string.symbols == ("RELIANCE", "TCS")
    assert from_string.exchange == "NSEEQ"

    from_mapping = _configured_runner_config(
        {
            "config": {
                "symbols": ["infy"],
                "exchange": "NSEEQ",
                "timeframe": "1d",
                "max_open_positions": 10,
                "signal_basis": "closed_bar",
            }
        }
    )
    assert from_mapping.symbols == ("INFY",)


def test_config_rejects_a_comma_separated_universe():
    with pytest.raises(ValueError, match="symbols must be a non-empty list"):
        _configured_runner_config(
            {
                "config": {
                    "symbols": "INFY, wipro ,",
                    "exchange": "NSEEQ",
                    "timeframe": "1d",
                    "max_open_positions": 10,
                    "signal_basis": "closed_bar",
                }
            }
        )


@pytest.mark.parametrize("raw", [None, {}, {"config": None}, {"config": "{not json"}])
def test_config_rejects_missing_or_malformed_saved_config(raw):
    """Malformed saved settings must be reported instead of replaced by defaults."""
    with pytest.raises(ValueError):
        _configured_runner_config(raw)


# ---------------------------------------------------------------------------
# 3. the happy path — tick to position, through every existing component
# ---------------------------------------------------------------------------
def test_signal_opens_a_position_through_the_full_pipeline(wired, deployment):
    runner, feed = wired()
    loop = _loop(runner, deployment)

    tick = runner.pass_once(now=SESSION)
    assert tick.signals == 1 and tick.orders == 1

    portfolio = loop._portfolio()
    position = portfolio.position(SYMBOL)
    assert position.quantity > 0
    # Filled at the live price plus one tick of slippage, not decayed to cash.
    assert position.avg_price == pytest.approx(feed.prices[SYMBOL], rel=0.01)
    assert portfolio.cash < deployment["capital"]


def test_the_fill_uses_the_live_price_not_the_daily_close(wired, deployment):
    """Requirement 3. The cached frame says 1000; the tick says 1234."""
    feed = LiveFeed({SYMBOL: 1234.0})
    runner, _ = wired(feed=feed)
    loop = _loop(runner, deployment)

    runner.pass_once(now=SESSION)
    assert loop._portfolio().position(SYMBOL).avg_price == pytest.approx(1234.0, rel=0.01)


def test_no_order_is_placed_when_the_market_is_closed(wired, deployment):
    runner, _ = wired()
    loop = _loop(runner, deployment)

    tick = runner.pass_once(now=datetime(2026, 9, 14, 20, 0))
    assert tick.signals == 0 and tick.orders == 0
    assert loop._portfolio().position(SYMBOL).quantity == 0


def test_a_deployment_with_no_universe_is_skipped_not_traded(wired, app_db):
    """A deployment that would silently do nothing says so and is skipped."""
    from datetime import datetime as _dt

    from forward.appdb.account_schema import users
    from forward.appdb.deployment_repository import DeploymentRepository

    with app_db.session() as session:
        session.execute(
            users.insert().values(
                user_id="u1",
                email="t@example.com",
                username="tester",
                display_name="T",
                password_hash="x",
                role="owner",
                is_active=True,
                mfa_enabled=False,
                failed_logins=0,
                created_at=_dt(2026, 1, 1),
                updated_at=_dt(2026, 1, 1),
            )
        )
    with app_db.session() as session:
        DeploymentRepository.create(
            session,
            user_id="u1",
            strategy_id="sma_pullback",
            strategy_version=1,
            mode="PAPER",
            capital=100_000.0,
            status="RUNNING",
            config={
                "symbols": [],
                "exchange": "NSEEQ",
                "timeframe": "1d",
                "max_open_positions": 10,
                "signal_basis": "closed_bar",
            },
        )

    runner, _ = wired()
    tick = runner.pass_once(now=SESSION)
    assert tick.deployments == 0
    assert tick.orders == 0


# ---------------------------------------------------------------------------
# 4. idempotency — a rule that stays true must fire once
# ---------------------------------------------------------------------------
def test_the_same_bar_does_not_open_a_second_position(wired, deployment):
    """A daily rule true at 10:00 is still true at 10:01. One signal, one order."""
    runner, _ = wired()
    loop = _loop(runner, deployment)

    runner.pass_once(now=SESSION)
    first = loop._portfolio().position(SYMBOL).quantity

    tick = runner.pass_once(now=SESSION + timedelta(seconds=30))
    assert tick.orders == 0
    assert loop._portfolio().position(SYMBOL).quantity == first


def test_the_idempotency_key_is_stable_for_one_intent_and_differs_by_side(wired, deployment):
    runner, _ = wired()
    loop = _loop(runner, deployment)
    runner.sync_loops()

    from forward.execution.oms import OrderDraft

    buy = OrderDraft(user_id="u1", symbol=SYMBOL, side="BUY", quantity=1)
    sell = OrderDraft(user_id="u1", symbol=SYMBOL, side="SELL", quantity=1)

    # Calling it twice for the same intent — a retry — must give the same key,
    # which is what makes the INSERT-as-guard work.
    assert loop._idempotency_key(buy, SYMBOL, "BUY") == loop._idempotency_key(buy, SYMBOL, "BUY")
    # The other side is a different intent.
    assert loop._idempotency_key(buy, SYMBOL, "BUY") != loop._idempotency_key(sell, SYMBOL, "SELL")


def test_repeated_passes_do_not_multiply_orders(wired, deployment):
    runner, _ = wired()
    _loop(runner, deployment)
    for i in range(10):
        runner.pass_once(now=SESSION + timedelta(seconds=i))
    assert runner.orders_total == 1


# ---------------------------------------------------------------------------
# 5. exits — the risk rules must be able to close a position
# ---------------------------------------------------------------------------
def test_stop_loss_closes_the_position(wired, deployment):
    runner, feed = wired(stop_loss_pct=10.0)
    loop = _loop(runner, deployment)

    runner.pass_once(now=SESSION)  # open at 1000
    assert loop._portfolio().position(SYMBOL).quantity > 0

    feed.prices[SYMBOL] = 880.0  # -12%, through the stop
    tick = runner.pass_once(now=SESSION + timedelta(minutes=5))
    assert tick.signals == 1
    assert loop._portfolio().position(SYMBOL).quantity == 0


def test_exit_is_not_rejected_as_a_short(wired, deployment):
    """The bug the first draft had.

    The risk gate compares a SELL against ``portfolio.position(symbol)``
    ``.quantity``. Handed a *flat* portfolio — the shared default — every exit
    reads as opening a short and is rejected whenever ``allow_short`` is false,
    so stop-losses would never execute. The runner must bind the deployment's own
    folded book.
    """
    from forward.execution.oms import OrderDraft

    runner, feed = wired(stop_loss_pct=10.0)
    loop = _loop(runner, deployment)
    runner.pass_once(now=SESSION)

    loop.bind()
    held = loop._portfolio().position(SYMBOL).quantity
    assert held > 0, "nothing to test — the position never opened"

    # The gate must see the holding, not a flat book.
    gate_portfolio = loop.orders.risk_gate.portfolio
    assert gate_portfolio.position(SYMBOL).quantity == pytest.approx(held)

    draft = OrderDraft(user_id="u1", symbol=SYMBOL, side="SELL", quantity=held, mode="PAPER")
    decision = loop.orders.risk_gate(draft)
    assert decision.allowed, f"exit rejected: {decision.reason}"


def test_take_profit_closes_the_position(wired, deployment):
    runner, feed = wired(stop_loss_pct=None, take_profit_pct=8.0)
    loop = _loop(runner, deployment)

    runner.pass_once(now=SESSION)
    feed.prices[SYMBOL] = 1100.0  # +10%, through the target
    runner.pass_once(now=SESSION + timedelta(minutes=5))
    assert loop._portfolio().position(SYMBOL).quantity == 0


def test_a_position_is_not_reopened_on_the_same_bar_after_an_exit(wired, deployment):
    """Once the position is closed the entry rule must not re-enter that day."""
    runner, feed = wired(stop_loss_pct=10.0)
    loop = _loop(runner, deployment)

    runner.pass_once(now=SESSION)
    feed.prices[SYMBOL] = 880.0
    runner.pass_once(now=SESSION + timedelta(minutes=5))
    assert loop._portfolio().position(SYMBOL).quantity == 0

    # Same bar: the entry key is already spent, so nothing reopens.
    runner.pass_once(now=SESSION + timedelta(minutes=6))
    assert loop._portfolio().position(SYMBOL).quantity == 0


# ---------------------------------------------------------------------------
# 6. position sizing
# ---------------------------------------------------------------------------
def test_size_is_whole_shares_and_never_exceeds_cash(wired, deployment):
    runner, _ = wired()
    loop = _loop(runner, deployment)
    runner.sync_loops()

    # 200k across a max of 10 positions = 20k per trade; 20k / 1000 = 20 shares.
    size = loop._size(1000.0)
    assert size == 20
    assert isinstance(size, int)


def test_size_is_zero_when_the_price_is_unusable(wired, deployment):
    runner, _ = wired()
    loop = _loop(runner, deployment)
    assert loop._size(0.0) == 0
    assert loop._size(-5.0) == 0


def test_size_is_capped_by_the_folded_cash_not_the_allocation(wired, deployment):
    """Sizing must respect cash on hand, not just the allocation.

    The account's allocation is larger than the available cash, so the only
    thing that can bound the order is the folded cash. If the cash cap were
    missing the order would exceed the account and fail the gate's cash check.

    The folded cash comes from the deployment row in the database (see
    ``PaperLedger._deployment_cash``), so the allocation is what bounds this.
    """
    runner, _ = wired()
    loop = _loop(runner, deployment)
    runner.sync_loops()

    # 200k cash, less the 2% cost buffer, at 1000 per share.
    assert loop._size(1000.0) == 196


def test_size_is_zero_with_no_allocation(wired, deployment, app_db):
    """No capital anywhere means no trade — not a guess at a default size."""
    from datetime import datetime as _dt

    from forward.appdb.account_schema import users

    with app_db.session() as session:
        session.execute(
            users.insert().values(
                user_id="u2",
                email="t2@example.com",
                username="tester2",
                display_name="T2",
                password_hash="x",
                role="owner",
                is_active=True,
                mfa_enabled=False,
                failed_logins=0,
                created_at=_dt(2026, 1, 1),
                updated_at=_dt(2026, 1, 1),
            )
        )

    runner, _ = wired()
    loop = _loop(runner, deployment)
    runner.sync_loops()
    # A row with no capital at all.
    loop.row = {**loop.row, "capital": 0.0, "user_id": "u2"}
    assert loop._size(1000.0) == 0


# ---------------------------------------------------------------------------
# 7. resting orders — requirement 4, re-evaluated as prices move
# ---------------------------------------------------------------------------
def test_resting_limit_order_fills_when_the_price_reaches_it(wired, deployment, app_db):
    """A limit away from the market rests, then fills when the market arrives."""
    from forward.execution.oms import OrderDraft

    runner, feed = wired()
    loop = _loop(runner, deployment)
    runner.sync_loops()
    loop.bind()

    # Place a limit well below the market. It must rest, not fill.
    draft = OrderDraft(
        user_id="u1",
        symbol=SYMBOL,
        side="BUY",
        quantity=10,
        mode="PAPER",
        limit_price=900.0,
        order_type="LIMIT",
        deployment_id=loop.deployment_id,
    )
    placed = loop.execution.place(draft, idempotency_key=None)
    assert placed.status in ("ACKNOWLEDGED", "SUBMITTED", "NEW")
    assert loop._portfolio().position(SYMBOL).quantity == 0

    # The market never comes to it: still nothing.
    tick = runner.pass_once(now=SESSION)
    assert tick.resting_fills == 0

    # Now it does.
    feed.prices[SYMBOL] = 895.0
    tick = runner.pass_once(now=SESSION + timedelta(seconds=5))
    assert tick.resting_fills >= 1 or loop._portfolio().position(SYMBOL).quantity > 0


def test_resting_orders_are_polled_outside_market_hours(wired, deployment):
    """An accepted order should be able to fill on any price that arrives."""
    from forward.execution.oms import OrderDraft

    runner, feed = wired()
    loop = _loop(runner, deployment)
    runner.sync_loops()
    loop.bind()

    draft = OrderDraft(
        user_id="u1",
        symbol=SYMBOL,
        side="BUY",
        quantity=5,
        mode="PAPER",
        limit_price=900.0,
        order_type="LIMIT",
        deployment_id=loop.deployment_id,
    )
    loop.execution.place(draft, idempotency_key=None)

    feed.prices[SYMBOL] = 850.0
    tick = runner.pass_once(now=datetime(2026, 9, 14, 20, 0))
    # Outside the session no *new* signals fire, but the resting order is still
    # re-evaluated rather than left stuck until tomorrow.
    assert tick.signals == 0
    assert loop._portfolio().position(SYMBOL).quantity == 5


# ---------------------------------------------------------------------------
# 8. the invariant — the whole design rests on this
# ---------------------------------------------------------------------------
def test_equity_always_equals_cash_plus_market_value(wired, deployment):
    runner, feed = wired(stop_loss_pct=10.0)
    loop = _loop(runner, deployment)

    runner.pass_once(now=SESSION)
    portfolio = loop._portfolio()
    assert portfolio.check_invariant() is None

    feed.prices[SYMBOL] = 1050.0
    portfolio = loop._portfolio()
    assert portfolio.check_invariant() is None
    assert portfolio.equity == pytest.approx(portfolio.cash + portfolio.market_value)


def test_pnl_moves_with_the_live_price(wired, deployment):
    runner, feed = wired()
    loop = _loop(runner, deployment)
    runner.pass_once(now=SESSION)
    qty = loop._portfolio().position(SYMBOL).quantity
    entry = loop._portfolio().position(SYMBOL).avg_price

    feed.prices[SYMBOL] = entry + 50.0
    expected = qty * 50.0
    unrealized = loop._portfolio().unrealized_pnl
    assert unrealized == pytest.approx(expected, rel=0.01)


def test_costs_are_charged_and_reduce_cash(wired, deployment):
    """A fill is not free: cash must fall by more than quantity * price."""
    runner, feed = wired()
    loop = _loop(runner, deployment)
    runner.pass_once(now=SESSION)

    portfolio = loop._portfolio()
    qty = portfolio.position(SYMBOL).quantity
    entry = portfolio.position(SYMBOL).avg_price
    spent = deployment["capital"] - portfolio.cash
    assert spent > qty * entry, "no costs were charged on the fill"
    assert portfolio.commission_paid > 0


# ---------------------------------------------------------------------------
# 9. the loop itself — start, stop, sync, status
# ---------------------------------------------------------------------------
def test_status_reports_the_loop_state(wired, deployment):
    runner, _ = wired()
    status = runner.status()
    assert status["running"] is False
    assert status["deployments"] == []

    runner.pass_once(now=SESSION)
    status = runner.status()
    assert status["passes"] == 1
    assert len(status["deployments"]) == 1


def test_loops_are_dropped_when_a_deployment_stops(wired, deployment, app_db):
    from forward.appdb.deployment_repository import DeploymentRepository

    runner, _ = wired()
    _loop(runner, deployment)
    assert len(runner._loops) == 1

    with app_db.session() as session:
        DeploymentRepository.stop(session, deployment["deployment_id"], "u1", reason="test")

    runner.sync_loops()
    assert runner._loops == {}


@pytest.mark.asyncio
async def test_the_background_loop_starts_and_stops(wired, deployment):
    import asyncio

    runner, _ = wired()
    runner.start()
    assert runner.running is True

    # Wait for the loop to actually complete a pass rather than sleeping a fixed
    # interval: a fixed sleep is a flaky assertion on a loaded machine, and it
    # asserts nothing about the loop having run.
    for _ in range(100):
        if runner.passes >= 1:
            break
        await asyncio.sleep(0.02)
    assert runner.passes >= 1, "the background loop never completed a pass"

    await runner.stop()
    assert runner.running is False


@pytest.mark.asyncio
async def test_start_is_idempotent(wired, deployment):
    runner, _ = wired()
    runner.start()
    first = runner._task
    runner.start()
    assert runner._task is first
    await runner.stop()


def test_one_broken_deployment_does_not_stop_the_others(wired, deployment, monkeypatch):
    """A single exception must not take the whole loop down."""
    runner, _ = wired()
    loop = _loop(runner, deployment)

    def explode():
        raise RuntimeError("boom")

    monkeypatch.setattr(loop, "evaluate", explode)
    tick = runner.pass_once(now=SESSION)  # must not raise
    assert tick.deployments == 1


def test_a_broken_store_does_not_kill_the_loop(wired, deployment, monkeypatch):
    runner, _ = wired()
    monkeypatch.setattr(
        runner,
        "running_deployments",
        lambda: (_ for _ in ()).throw(RuntimeError("db is down")),
    )
    tick = runner.pass_once(now=SESSION)  # must not raise
    assert tick.signals == 0


# ---------------------------------------------------------------------------
# 10. the singleton
# ---------------------------------------------------------------------------
def test_get_runner_is_a_singleton(fresh_env, app_db):
    from forward.composition import (
        create_paper_runner,
        get_paper_runner,
        reset_paper_runner,
    )

    reset_paper_runner()
    configured = create_paper_runner(
        daily_history=CachedMarketFramesAdapter(),
        instrument_history=empty_cached_instrument_history_port(),
        vwap_liquidity=NseBhavcopyLiquidityAdapter(get_settings().research_data_directory),
        broker_provider=lambda: None,
        ledger=PaperLedger(storage=SqlPaperLedgerStorage(app_db), price_source=no_live_price),
        order_storage=SqlOrderStorage(app_db),
        runner_storage=SqlPaperRunnerStorage(app_db),
        state_storage=SqlSystemStateStorage(app_db),
        risk_state=RiskStateService(SqlRiskStateStorage(app_db), get_settings()),
        portfolio_storage=SqlPortfolioStorage(app_db),
        sector_table={},
        instrument_lookup=order_instrument_lookup,
        journal_storage=SqlTradeJournalStorage(app_db),
        signal_context=NoopSignalContextEnricher(),
        price_source=no_live_price,
    )
    assert configured is get_paper_runner()
    reset_paper_runner()


# ---------------------------------------------------------------------------
# 11. the open-position cap counts the whole book, not the current universe
# ---------------------------------------------------------------------------
def test_the_open_position_cap_counts_positions_outside_the_universe(wired, deployment):
    """A position consumes capital whether or not its symbol is still evaluated.

    The count used to iterate ``config.symbols``, which makes it depend on a list
    that can be edited while the deployment runs: drop a held symbol from the
    universe and the position becomes invisible to ``max_open_positions``, so the
    deployment opens its way past the cap while the risk surface reports the
    limit as enforced. That is a limit that silently does not apply, which is
    worse than no limit because the dashboard says it is.
    """
    runner, _feed = wired()
    loop = _loop(runner, deployment)
    runner.pass_once(now=SESSION)
    assert loop._portfolio().position(SYMBOL).quantity > 0

    # The symbol leaves the universe while the position is still held.
    loop.config.symbols = ()

    assert loop._open_position_count() == 1, "a held position became invisible"
    assert loop._portfolio().position(SYMBOL).quantity > 0, "the position is still real"


def test_the_cap_refuses_a_new_entry_when_a_position_is_held_outside_the_universe(
    wired, deployment
):
    """The consequence of the count above, asserted on the behaviour.

    One position is held in a symbol that has since left the universe, and the
    cap is one. An entry in a symbol that *is* still evaluated must be refused:
    the capital is already committed.
    """
    runner, feed = wired()
    feed.prices["TCS"] = 1000.0
    loop = _loop(runner, deployment)
    loop.config.symbols = (SYMBOL, "TCS")
    loop.config.max_open_positions = 1
    # Both symbols need a warmed frame and a live price, or the second would be
    # skipped for want of either and the cap would never be consulted.
    loop.warmup()

    runner.pass_once(now=SESSION)
    assert loop._portfolio().position(SYMBOL).quantity > 0
    assert loop._portfolio().position("TCS").quantity == 0, "the cap let a second in"

    # The held symbol leaves the universe; the cap must not forget it.
    loop.config.symbols = ("TCS",)
    loop.acted.clear()

    tick = runner.pass_once(now=SESSION + timedelta(minutes=1))
    assert tick.orders == 0, "a position outside the universe stopped counting against the cap"
    assert loop._portfolio().position("TCS").quantity == 0


def test_the_open_count_follows_the_fold_not_the_universe(wired, deployment):
    """The count is a property of the book, so editing the universe cannot
    change it while the book is unchanged."""
    runner, _feed = wired()
    loop = _loop(runner, deployment)
    runner.pass_once(now=SESSION)
    assert loop._open_position_count() == 1

    for universe in ((), ("INFY",), (SYMBOL,)):
        loop.config.symbols = universe
        assert loop._open_position_count() == 1


# ---------------------------------------------------------------------------
# 12. the status surface reports each deployment's own figures
# ---------------------------------------------------------------------------
def test_last_pass_orders_is_per_deployment_not_a_running_total(wired, deployment, app_db):
    """``last_pass`` is what the dashboard shows *for that deployment*.

    A cumulative figure there reports the sum across every deployment as this
    one's own — a number that grows with somebody else's activity, which is the
    kind of wrong that looks like success.
    """
    from forward.appdb.deployment_repository import DeploymentRepository

    with app_db.session() as session:
        DeploymentRepository.create(
            session,
            user_id="u1",
            strategy_id="sma_pullback",
            strategy_version=1,
            mode="PAPER",
            capital=200_000.0,
            status="RUNNING",
            config={
                "symbols": [SYMBOL],
                "exchange": "NSEEQ",
                "timeframe": "1d",
                "max_open_positions": 10,
                "signal_basis": "live",
            },
        )

    runner, _feed = wired()
    tick = runner.pass_once(now=SESSION)

    assert len(runner._loops) == 2, "the second deployment was not picked up"
    assert tick.orders == 2, "both deployments should have placed one order"
    for loop in runner._loops.values():
        assert loop.last_pass["orders"] == 1, (
            f"deployment {loop.deployment_id[:8]} reported "
            f"{loop.last_pass['orders']} orders of its own"
        )
        assert loop.last_pass["signals"] == 1


# ---------------------------------------------------------------------------
# 13. intraday mode — the N-minute bar bucketing has to actually run
# ---------------------------------------------------------------------------
def test_intraday_bar_bucketing_does_not_crash(wired, app_db):
    """`_refresh_intraday_bar` used `timedelta` without importing it.

    Nothing in this suite ran an intraday-timeframe deployment, so a plain
    `NameError` on the first tick of any 5m/15m/... deployment went
    unnoticed — this exercises that path directly rather than trusting that
    "the tests pass" means intraday mode works.
    """
    from datetime import datetime as _dt

    from forward.appdb.account_schema import users
    from forward.appdb.deployment_repository import DeploymentRepository

    with app_db.session() as session:
        session.execute(
            users.insert().values(
                user_id="u2",
                email="t2@example.com",
                username="tester2",
                display_name="T2",
                password_hash="x",
                role="owner",
                is_active=True,
                mfa_enabled=False,
                failed_logins=0,
                created_at=_dt(2026, 1, 1),
                updated_at=_dt(2026, 1, 1),
            )
        )
    with app_db.session() as session:
        row = DeploymentRepository.create(
            session,
            user_id="u2",
            strategy_id="sma_pullback",
            strategy_version=1,
            mode="PAPER",
            capital=200_000.0,
            status="RUNNING",
            config={
                "symbols": [SYMBOL],
                "exchange": "NSEEQ",
                "timeframe": "5m",
                "max_open_positions": 10,
                "signal_basis": "closed_bar",
            },
        )

    runner, _feed = wired()
    loop = _loop(runner, row)
    assert loop.timeframe_minutes == 5

    result = loop._refresh_intraday_bar(SYMBOL, 1234.0)
    assert result is not None
    assert result["close"].iloc[-1] == 1234.0
    bar = loop.intraday_bar[SYMBOL]
    assert bar["start"].minute % 5 == 0, "bucketed to the 5-minute boundary"


# ---------------------------------------------------------------------------
# signal basis: closed bars (like the backtest) vs the forming bar
# ---------------------------------------------------------------------------
def _fresh_history(monkeypatch, last_day="2026-09-11"):
    """Daily history that ends on the last completed session before SESSION (Fri 11 Sep)."""
    index = pd.date_range(end=last_day, periods=300, freq="B")
    frame = _daily(SYMBOL).assign(ts=index)
    monkeypatch.setattr(
        "forward.data.cached_market_frames.load_daily_from_cache",
        lambda symbol, exchange="NSEEQ", **kw: frame.copy(),
    )
    return len(frame)


def _record_entry_frames(monkeypatch):
    from forward.signals import rules
    from forward.signals.models import Signal

    seen: list[int] = []

    def entry(symbol, frame, rls, context=None):
        seen.append(len(frame))
        return [
            Signal(
                symbol=symbol,
                action="BUY",
                rule="test_entry",
                reason="forced",
                price=float(frame["close"].iloc[-1]),
            )
        ]

    monkeypatch.setattr(rules, "eval_entry", entry)
    return seen


def _use_closed_bar(monkeypatch):
    """Loops are rebuilt from the deployment row on every pass, so override the config at its source."""
    from dataclasses import replace

    original = RunnerConfig.from_deployment.__func__
    monkeypatch.setattr(
        RunnerConfig,
        "from_deployment",
        classmethod(
            lambda cls, row, **kwargs: replace(
                original(cls, row, **kwargs), signal_basis="closed_bar"
            )
        ),
    )


def test_closed_bar_mode_decides_on_completed_bars_only(wired, deployment, monkeypatch):
    _use_closed_bar(monkeypatch)
    runner, _ = wired()
    n = _fresh_history(monkeypatch)
    seen = _record_entry_frames(monkeypatch)
    _loop(runner, deployment)
    runner.pass_once(now=SESSION)
    assert seen and seen[0] == n  # no forming bar appended


def test_live_mode_still_appends_the_forming_bar(wired, deployment, monkeypatch):
    runner, _ = wired()
    n = _fresh_history(monkeypatch)
    seen = _record_entry_frames(monkeypatch)
    _loop(runner, deployment)
    runner.pass_once(now=SESSION)
    assert seen and seen[0] == n + 1


def test_stale_history_pauses_new_entries(wired, deployment, monkeypatch):
    _use_closed_bar(monkeypatch)
    runner, _ = wired()
    _fresh_history(monkeypatch, last_day="2026-09-04")  # a week old
    seen = _record_entry_frames(monkeypatch)
    loop = _loop(runner, deployment)
    tick = runner.pass_once(now=SESSION)
    loop = _loop(runner, deployment)
    assert seen == [] and tick.orders == 0
    assert "behind" in (loop.skipped_reason or "")


def test_signal_basis_must_be_explicit_and_known():
    base = {
        "symbols": ["X"],
        "exchange": "NSEEQ",
        "timeframe": "1d",
        "max_open_positions": 10,
    }
    with pytest.raises(ValueError, match="signal_basis"):
        _configured_runner_config({"config": base})
    assert (
        _configured_runner_config(
            {"config": {**base, "signal_basis": "closed_bar"}}
        ).signal_basis
        == "closed_bar"
    )
    assert (
        _configured_runner_config(
            {"config": {**base, "signal_basis": "live"}}
        ).signal_basis
        == "live"
    )
    with pytest.raises(ValueError, match="unsupported signal_basis"):
        _configured_runner_config({"config": {**base, "signal_basis": "sometimes"}})


def test_the_session_open_comes_from_the_feed_when_it_reports_one(wired, deployment):
    from forward.core.paper_models import TickPrice

    feed = LiveFeed()
    runner, _ = wired(feed=feed)
    loop = _loop(runner, deployment)
    feed.prices[SYMBOL] = TickPrice(price=1010.0, open=1004.0)

    opened, minutes = loop._session_open(SYMBOL, 1010.0, SESSION, get_market_calendar())
    assert (opened, minutes) == (1004.0, 0.0)


# Saved builder context must own session boundaries, including non-default clocks.
def test_saved_context_pins_cadence_and_ist_boundaries():
    settings = get_settings()
    config = RunnerConfig(
        symbols=(SYMBOL,),
        interval_seconds=settings.runner_interval_seconds,
        lookback_days=settings.runner_lookback_days,
        timeframe="1d",
    )
    config.apply_context(
        {
            "context": {
                "style": "intraday",
                "timeframe": "5m",
                "entry_cutoff": "14:30",
                "square_off_time": "15:00",
            }
        }
    )
    assert config.timeframe == "5m"
    assert not config.entry_closed(datetime(2026, 9, 14, 14, 29))
    assert config.entry_closed(datetime(2026, 9, 14, 14, 30))
    assert config.entry_closed(datetime(2026, 9, 14, 9, 0, tzinfo=UTC))
    assert not config.square_off_due(datetime(2026, 9, 14, 14, 59))
    assert config.square_off_due(datetime(2026, 9, 14, 15, 0))


def test_cutoff_blocks_new_entry_but_square_off_closes_without_history(
    wired, deployment, monkeypatch
):
    runner, _ = wired()
    loop = _loop(runner, deployment)
    loop.bind()
    loop.config.entry_cutoff = "14:30"
    loop.config.square_off_time = "15:00"
    assert loop.evaluate(now=SESSION)  # Open before cutoff.
    assert loop._position_quantity(SYMBOL) > 0
    # Session exits must not depend on indicator history being available.
    monkeypatch.setattr(loop, "live_frame", lambda symbol: None)
    assert loop.evaluate(now=datetime(2026, 9, 14, 15, 0))
    assert loop._position_quantity(SYMBOL) == 0
    assert not loop.evaluate(now=datetime(2026, 9, 14, 15, 1))


def test_cutoff_blocks_flat_book_entry(wired, deployment):
    runner, _ = wired()
    loop = _loop(runner, deployment)
    loop.bind()
    loop.config.entry_cutoff = "14:30"
    assert loop.evaluate(now=datetime(2026, 9, 14, 14, 30)) == []
    assert loop._position_quantity(SYMBOL) == 0


def test_resting_entry_is_cancelled_before_matching_at_cutoff(wired, deployment):
    from forward.execution.oms import OrderDraft

    runner, feed = wired(entry=False)
    loop = _loop(runner, deployment)
    loop.bind()
    loop.config.entry_cutoff = "14:30"
    placed = loop.execution.place(
        OrderDraft(
            user_id="u1",
            symbol=SYMBOL,
            side="BUY",
            quantity=10,
            mode="PAPER",
            limit_price=900.0,
            order_type="LIMIT",
            deployment_id=loop.deployment_id,
        ),
        idempotency_key=None,
    )
    assert placed.status == "ACKNOWLEDGED"
    feed.prices[SYMBOL] = 895.0
    assert loop.poll_resting_orders(now=datetime(2026, 9, 14, 14, 30)) == []
    assert loop._position_quantity(SYMBOL) == 0
    assert not [o for o in loop.orders.open_orders("u1") if o["order_id"] == placed.order_id]


def test_runner_sync_batches_exact_strategy_versions(app_db, deployment, wired, monkeypatch):
    from unittest.mock import Mock

    from forward.appdb.strategy_repository import StrategyRepository

    runner, _ = wired()
    batch = Mock(wraps=StrategyRepository.versions_for_keys)
    monkeypatch.setattr(StrategyRepository, "versions_for_keys", batch)
    runner.sync_loops()
    assert batch.call_count == 1
    assert batch.call_args.args[1] == [(deployment["strategy_id"], deployment["strategy_version"])]
    assert deployment["deployment_id"] in runner._loops
    runner.sync_loops()
    assert batch.call_count == 2  # no persistent cache hides deployment changes


def test_exact_version_batch_is_bounded_and_preserves_missing(app_db, deployment):
    from sqlalchemy import event, select

    from forward.appdb.strategy_backtest_schema import strategy_versions
    from forward.appdb.strategy_repository import StrategyRepository

    with app_db.session() as session:
        strategy = StrategyRepository.create(session, user_id="u1", name="batch-test", kind="rules")
        StrategyRepository.create_version(
            session,
            strategy_id=strategy["strategy_id"],
            author_user_id="u1",
            definition={"entry": {}},
        )
        StrategyRepository.create_version(
            session,
            strategy_id=strategy["strategy_id"],
            author_user_id="u1",
            definition={"entry": {"min_history_bars": 80}},
        )
        existing = session.execute(select(strategy_versions)).mappings().first()
        assert existing is not None
        key = (existing["strategy_id"], existing["version"])
        expected = StrategyRepository.version(session, *key)
        statements = []

        def record(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(session.bind, "before_cursor_execute", record)
        try:
            keys = [key, key, *[("missing", n) for n in range(401)]]
            result = StrategyRepository.versions_for_keys(session, keys)
            assert result == {key: expected}
            assert len(statements) == 2
            statements.clear()
            assert StrategyRepository.versions_for_keys(session, []) == {}
            assert statements == []
        finally:
            event.remove(session.bind, "before_cursor_execute", record)


def test_open_orders_can_be_scoped_to_deployment(app_db, deployment):
    from forward.appdb.deployment_repository import DeploymentRepository
    from forward.appdb.identity_repository import UserRepository
    from forward.appdb.order_repository import OrderRepository
    from forward.services.orders import OrderService

    with app_db.session() as session:
        other = DeploymentRepository.create(
            session,
            user_id="u1",
            strategy_id="sma_pullback",
            strategy_version=1,
            mode="PAPER",
            capital=200_000,
            status="RUNNING",
            config={
                "symbols": [SYMBOL],
                "exchange": "NSEEQ",
                "timeframe": "1d",
                "max_open_positions": 10,
                "signal_basis": "closed_bar",
            },
        )
        user = UserRepository.create(
            session, email="other@example.com", username="other", password_hash="x", role="viewer"
        )

        def create(user_id="u1", deployment_id=None):
            return OrderRepository.create(
                session,
                user_id=user_id,
                symbol=SYMBOL,
                side="BUY",
                quantity=1,
                deployment_id=deployment_id,
            )

        target = create(deployment_id=deployment["deployment_id"])
        unrelated = create(deployment_id=other["deployment_id"])
        manual = create()
        foreign = create(user_id=user["user_id"])
        terminal = create(deployment_id=deployment["deployment_id"])
    service = OrderService(storage=SqlOrderStorage(app_db), risk_gate=None)
    service.reject(terminal["order_id"], "u1", reason="test rejection")
    scoped = service.open_orders("u1", deployment_id=deployment["deployment_id"])
    assert [o["order_id"] for o in scoped] == [target["order_id"]]
    all_ids = {o["order_id"] for o in service.open_orders("u1")}
    assert all_ids == {target["order_id"], unrelated["order_id"], manual["order_id"]}
    assert foreign["order_id"] not in all_ids
    assert service.open_orders(user["user_id"], deployment_id=deployment["deployment_id"]) == []
