"""Lifecycle for quote-driven F&O forward paper runs."""

from __future__ import annotations

import math
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from loguru import logger

from forward.config.costs import commission_model
from forward.core.instrument_models import Instrument
from forward.core.live_feed_ports import LiveFeedConnectionPort
from forward.core.option_catalog import OptionContractCatalog
from forward.core.options_forward_execution_ports import OptionsForwardExecutionPort
from forward.core.time import utcnow
from forward.execution.risk import RiskLimits
from forward.live.runner import LiveConfig, LiveRunner
from forward.strategy.options import ConfiguredOptionsStrategy


class OptionsForwardRunner(LiveRunner):
    """Use the shared live-data runner while guaranteeing simulated orders."""

    def flatten(self, ctx) -> None:
        """Fold immediate simulated closing fills into the final paper ledger."""
        super().flatten(ctx)
        self._sync_broker_fills()

    def _submit(self, order):
        """Mark an option with the current quote before applying risk limits."""
        quote = self.feed.last_tick(order.instrument.symbol)
        if quote is not None and quote.last > 0:
            self.portfolio.ensure_position(order.instrument).mark(quote.last)
        return super()._submit(order)


@dataclass
class ForwardRun:
    """In-process handles for one active forward paper run."""

    run_id: str
    user_id: str
    runner: OptionsForwardRunner
    thread: threading.Thread
    error: str | None = None

    def snapshot(self) -> dict[str, Any]:
        """Return live paper state and simulated fills for the app monitor.

        Returns:
            The latest portfolio, with P&L marked incomplete when any open
            position lacks a live quote or a fill has unknown commission.
        """
        portfolio = self.runner.portfolio
        # Strategy decisions still consume completed bars, but the monitor marks
        # open positions from the latest tick so displayed P&L is not minute-lagged.
        unpriced_symbols: list[str] = []
        for symbol, position in portfolio.positions.items():
            if position.is_flat:
                continue
            quote = self.runner.feed.last_tick(symbol)
            if quote is not None and math.isfinite(quote.last) and quote.last > 0:
                position.mark(quote.last, quote.ts)
            else:
                unpriced_symbols.append(symbol)
        price_complete = not unpriced_symbols
        commission_complete = portfolio.commission_complete
        pnl_complete = price_complete and commission_complete
        equity = portfolio.equity if pnl_complete else None
        return {
            "equity": equity,
            "cash": portfolio.cash if commission_complete else None,
            "pnl": equity - portfolio.initial_cash if pnl_complete else None,
            "realized_pnl": portfolio.realized_pnl if commission_complete else None,
            "gross_realized_pnl": portfolio.realized_pnl,
            "unrealized_pnl": portfolio.unrealized_pnl if pnl_complete else None,
            "commission": portfolio.commission_paid if commission_complete else None,
            "pnl_complete": pnl_complete,
            "commission_complete": commission_complete,
            "price_complete": price_complete,
            "unpriced_symbols": unpriced_symbols,
            "positions": [
                position.model_dump(mode="json") for position in portfolio.positions.values()
            ],
            "fills": [
                fill.model_dump(mode="json") for fill in self.runner.broker.fills_from_trades()
            ],
            "updated_at": utcnow().isoformat(),
        }


class OptionsForwardService:
    """Start, inspect, and stop in-process F&O paper runners."""

    def __init__(self, execution: OptionsForwardExecutionPort, config: LiveConfig) -> None:
        """Create the manager with a quote and simulated-execution adapter.

        Args:
            execution: Builds the IIFL feed and paper broker.
            config: Required live-loop policy from application configuration.
        """
        self._execution = execution
        self._config = config
        self._lock = threading.RLock()
        self._runs: dict[str, ForwardRun] = {}

    def start(
        self,
        *,
        run_id: str,
        user_id: str,
        session: Any,
        instruments: dict[str, Instrument],
        catalog: OptionContractCatalog,
        definition: dict[str, Any],
        initial_cash: float,
        commission_per_contract: float,
        commission_minimum: float,
        transaction_cost_pct: float,
        commission_cap_pct: float,
        risk: RiskLimits,
        kill_switch_provider: Callable[[], bool],
        on_finish: Callable[[ForwardRun], None],
        bridge_client: LiveFeedConnectionPort,
    ) -> ForwardRun:
        """Build and start a quote-driven paper runner.

        Args:
            run_id: Stable identifier for the stored run.
            user_id: Account that owns the run.
            session: Authenticated broker session used for market data.
            instruments: Spot instruments watched when the run starts.
            catalog: Listed option contracts available to the strategy.
            definition: Saved options strategy definition.
            initial_cash: Starting simulated cash balance.
            commission_per_contract: Fee per contract for simulated fills.
            commission_minimum: Minimum fee per simulated order.
            transaction_cost_pct: Fee percentage applied to trade value.
            commission_cap_pct: Maximum fee percentage for one trade.
            risk: Risk limits enforced by the shared live runner.
            kill_switch_provider: Reads the current platform kill switch.
            on_finish: Persists the final run snapshot.
            bridge_client: Shared live market-data bridge connection.

        Returns:
            The in-process run handle.

        Raises:
            ValueError: If the account already has an active options run.
        """
        with self._lock:
            if any(run.user_id == user_id and run.thread.is_alive() for run in self._runs.values()):
                raise ValueError("this account already has an active options forward run")

            feed = self._execution.create_feed(session, instruments, bridge_client)
            commission = commission_model(
                per_contract=commission_per_contract,
                min_per_order=commission_minimum,
                pct_of_notional=transaction_cost_pct / 100,
                max_pct_of_trade=commission_cap_pct / 100,
            )
            broker = self._execution.create_paper_broker(feed, commission)
            runner = OptionsForwardRunner(
                instruments=instruments,
                strategy=ConfiguredOptionsStrategy(definition),
                broker=broker,
                feed=feed,
                config=self._config,
                risk=risk,
                initial_cash=initial_cash,
                kill_switch_provider=kill_switch_provider,
                contract_catalog=catalog,
            )
            handle = ForwardRun(
                run_id,
                user_id,
                runner,
                threading.Thread(
                    target=self._run,
                    args=(run_id, runner, on_finish),
                    name=f"options-paper-{run_id[:8]}",
                    daemon=True,
                ),
            )
            self._runs[run_id] = handle
            handle.thread.start()
        return handle

    def _run(
        self,
        run_id: str,
        runner: OptionsForwardRunner,
        on_finish: Callable[[ForwardRun], None],
    ) -> None:
        """Run the feed loop, record failures, and persist its final state.

        Args:
            run_id: Identifier of the active run.
            runner: The quote-driven paper runner.
            on_finish: Callback that stores the final paper snapshot.
        """
        try:
            runner.run()
        except Exception as exc:  # noqa: BLE001 - caller reads the persisted reason
            logger.exception("options forward run {} failed", run_id)
            with self._lock:
                self._runs[run_id].error = str(exc)
        finally:
            with self._lock:
                handle = self._runs[run_id]
            try:
                on_finish(handle)
            except Exception:  # noqa: BLE001 - persistence failure is visible in app logs
                logger.exception("could not persist final options forward state for {}", run_id)

    def get(self, run_id: str, user_id: str) -> ForwardRun | None:
        """Find an active or recently completed in-process run by owner."""
        with self._lock:
            run = self._runs.get(run_id)
            return run if run and run.user_id == user_id else None

    def stop(self, run_id: str, user_id: str) -> ForwardRun | None:
        """Request a graceful stop, which flattens through the simulated broker."""
        run = self.get(run_id, user_id)
        if run:
            run.runner.stop()
        return run
