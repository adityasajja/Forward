"""Run and persist configured options backtests."""

from __future__ import annotations

import json
import math
from typing import Any

import pandas as pd

from forward.backtest.engine import BacktestConfig, BacktestEngine
from forward.config.costs import commission_model, slippage_model
from forward.core.options_dataset_ports import OptionsDatasetPort
from forward.core.saved_strategy_ports import SavedStrategyPort
from forward.core.strategy_kinds import OPTIONS_STRATEGY_KIND
from forward.core.time import utcnow
from forward.services.options_runs import OptionsRunService
from forward.strategy.options import (
    ConfiguredOptionsStrategy,
    options_definition,
)


class OptionsBacktestService:
    """Execute F&O backtests for saved strategy versions and imported data."""

    def __init__(
        self,
        dataset_port: OptionsDatasetPort,
        runs: OptionsRunService,
        strategies: SavedStrategyPort,
        *,
        max_curve_points: int,
        max_persisted_trades: int,
    ) -> None:
        """Create the service with its historical-data adapter.

        Args:
            dataset_port: Loads the selected historical bars and instruments.
            runs: Stores backtest state and results.
            strategies: Reads saved strategies and their exact versions.
            max_curve_points: Maximum points kept in one result curve.
            max_persisted_trades: Maximum trade rows kept for one result.
        """
        self._dataset_port = dataset_port
        self._runs = runs
        self._strategies = strategies
        self._max_curve_points = max_curve_points
        self._max_persisted_trades = max_persisted_trades

    def run(self, run_id: str, user_id: str, request_data: dict[str, Any]) -> None:
        """Run a queued options backtest and store its final result.

        Args:
            run_id: Identifier of the queued backtest record.
            user_id: Owner of the run and selected data set.
            request_data: Validated strategy, data, capital, margin, and cost settings.
        """
        store = self._runs
        try:
            strategy_id = request_data["strategy_id"]
            version_number = request_data["version"]
            strategy_record = self._strategies.get(user_id, strategy_id)
            if strategy_record["kind"] != OPTIONS_STRATEGY_KIND:
                raise ValueError("the selected saved strategy version is not F&O")
            strategy = self._strategies.version(user_id, strategy_id, version_number)
            config = options_definition(strategy["definition"])
            store.update_backtest(run_id, user_id, status="RUNNING")
            dataset = store.dataset(user_id, request_data["dataset_id"])
            if dataset is None:
                raise ValueError("options dataset not found for this account")
            bars_path = dataset["bars_path"]
            instruments_path = dataset["instruments_path"]
            feed = self._dataset_port.load(bars_path, instruments_path)
            symbols = set(feed.frame["symbol"].str.upper().unique().to_list())
            if config.spot_symbol not in symbols:
                raise ValueError(f"dataset is missing configured spot symbol {config.spot_symbol}")
            missing_margin = [
                instrument.symbol
                for instrument in feed.instruments.values()
                if instrument.is_option and instrument.initial_margin_per_unit <= 0
            ]
            margin_mode = request_data["margin_mode"]
            if margin_mode == "enforced" and missing_margin:
                raise ValueError(
                    "options catalog must provide positive initial_margin_per_unit "
                    "for every option; "
                    f"missing for {missing_margin[:20]}"
                )
            result = BacktestEngine(
                feed,
                ConfiguredOptionsStrategy(config.model_dump(mode="json")),
                BacktestConfig(
                    initial_cash=request_data["initial_cash"],
                    commission=commission_model(
                        per_contract=request_data["commission_per_contract"],
                        min_per_order=request_data["commission_minimum"],
                        pct_of_notional=request_data["transaction_cost_pct"] / 100,
                        max_pct_of_trade=request_data["commission_cap_pct"] / 100,
                    ),
                    slippage=slippage_model(bps=request_data["slippage_bps"]),
                    fill_on_next_open=request_data["fill_on_next_open"],
                    enforce_margin=margin_mode == "enforced",
                    allow_short=request_data["allow_short"],
                ),
            ).run()
            equity = result.equity
            curve_step = max(1, math.ceil(len(equity) / self._max_curve_points))
            sampled_equity = equity.iloc[::curve_step]
            if not equity.empty and sampled_equity.index[-1] != equity.index[-1]:
                sampled_equity = pd.concat([sampled_equity, equity.iloc[-1:]])
            trade_records = json.loads(result.trades.to_json(orient="records", date_format="iso"))
            payload = {
                "metrics": self._metrics_for_json(result.metrics),
                "equity": [
                    {"ts": ts.isoformat(), "value": float(value)}
                    for ts, value in sampled_equity.items()
                ],
                "trades": trade_records[: self._max_persisted_trades],
                "trades_truncated": len(trade_records) > self._max_persisted_trades,
                "strategy": {"strategy_id": strategy_id, "version": version_number},
                "data": {
                    "dataset_id": request_data["dataset_id"],
                    "fingerprint": self._dataset_port.fingerprint(bars_path, instruments_path),
                },
                "assumptions": {
                    "margin_mode": margin_mode,
                    "fill_on_next_open": request_data["fill_on_next_open"],
                    "allow_short": request_data["allow_short"],
                    "capital_feasibility": (
                        "not modeled" if margin_mode == "pnl_only" else "margin metadata required"
                    ),
                    "execution_costs": {
                        "commission_per_contract": request_data["commission_per_contract"],
                        "commission_minimum": request_data["commission_minimum"],
                        "transaction_cost_pct": request_data["transaction_cost_pct"],
                        "commission_cap_pct": request_data["commission_cap_pct"],
                        "slippage_bps": request_data["slippage_bps"],
                    },
                },
            }
            store.update_backtest(
                run_id,
                user_id,
                status="COMPLETED",
                result=json.dumps(payload, default=str),
                finished_at=utcnow(),
            )
        except Exception as exc:  # noqa: BLE001 - the UI reads the exact recorded failure
            store.update_backtest(
                run_id,
                user_id,
                status="FAILED",
                error=str(exc),
                finished_at=utcnow(),
            )

    @staticmethod
    def _metrics_for_json(metrics: Any) -> dict[str, Any]:
        """Convert metrics to finite JSON values, preserving undefined as null."""
        result: dict[str, Any] = {}
        for key, value in metrics.as_dict().items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                result[key] = value if math.isfinite(float(value)) else None
            else:
                result[key] = value
        return result
