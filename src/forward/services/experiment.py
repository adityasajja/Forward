"""Strategy Experiment Lab Service.

Objective side-by-side comparison:
CURRENT STRATEGY vs LEARNED CANDIDATE vs BASELINE
→ Evaluated under identical assumptions: dates, universe, capital, costs, slippage.
→ Deep comparative metrics: returns, max DD, Sharpe, Sortino, profit factor, win rate, trades, median trade, OOS efficiency.
→ Curve comparisons: daily equity curves, drawdown curves, monthly returns, trade-return distributions, regime performance.
→ Structured explanations: WHAT CHANGED, WHY PROPOSED, WHAT EXPERIMENT FOUND.
→ Zero autonomous deployment: strictly produces research findings & approvals.
"""

from __future__ import annotations

import json
import math
from typing import Any

import pandas as pd
from loguru import logger

from forward.backtest.engine import BacktestConfig
from forward.core.backtest_ports import (
    BacktestFeedPort,
    HistoricalFeedPort,
)
from forward.core.strategy_research_ports import (
    StrategyResearchStoragePort,
)
from forward.optimization.adaptive import (
    apply_parameter_to_definition,
    extract_adaptive_parameters,
    locate_parameter,
)
from forward.optimization.evaluator import (
    RobustnessResult,
    evaluate_backtest,
    evaluate_robustness,
    evaluate_walk_forward,
)
from forward.research.validate import WalkForwardConfig


def _round_float(val: Any, decimals: int) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return round(f, decimals)
    except (ValueError, TypeError):
        return None


def _calc_curve_points(series: pd.Series, max_points: int, decimals: int) -> list[dict[str, Any]]:
    """Sample time-series curve to reasonable point count for frontend visualization."""
    if series.empty:
        return []
    s = series.dropna()
    total = len(s)
    step = max(1, total // max_points) if total > max_points else 1
    sampled = s.iloc[::step]
    if total > 0 and (total - 1) not in sampled.index:
        sampled = pd.concat([sampled, s.iloc[[-1]]])

    points = []
    for dt, val in sampled.items():
        ts_str = dt.isoformat() if hasattr(dt, "isoformat") else str(dt)
        points.append({"ts": ts_str, "value": round(float(val), decimals)})
    return points


def _calc_drawdown_curve(equity_series: pd.Series) -> pd.Series:
    """Compute underwater drawdown series in percent."""
    if equity_series.empty:
        return pd.Series(dtype=float)
    peak = equity_series.cummax()
    dd = (equity_series - peak) / peak * 100.0
    return dd


def _calc_monthly_returns(equity_series: pd.Series, decimals: int) -> list[dict[str, Any]]:
    """Calculate monthly return grid from daily equity series."""
    if equity_series.empty:
        return []
    resampled = equity_series.resample("ME").last()
    if len(resampled) < 2:
        return []
    pcts = resampled.pct_change().dropna() * 100.0
    return [
        {
            "year": int(timestamp.year),
            "month": int(timestamp.month),
            "return_pct": round(float(return_pct), decimals),
        }
        for timestamp, return_pct in pcts.items()
    ]


def _calc_return_distribution(trades_df: pd.DataFrame, decimals: int) -> dict[str, Any]:
    """Calculate histogram and moment statistics of trade return distribution."""
    pnl_col = "return_pct" if "return_pct" in trades_df.columns else ("pnl_pct" if "pnl_pct" in trades_df.columns else None)
    if trades_df.empty or not pnl_col:
        return {
            "buckets": [],
            "mean": 0.0,
            "median": 0.0,
            "std": 0.0,
            "skew": 0.0,
            "kurtosis": 0.0,
        }

    pnl = trades_df[pnl_col].dropna()
    if len(pnl) == 0:
        return {"buckets": [], "mean": 0.0, "median": 0.0, "std": 0.0, "skew": 0.0, "kurtosis": 0.0}

    bins = [-float("inf"), -5.0, -2.0, -1.0, 0.0, 1.0, 2.0, 5.0, float("inf")]
    labels = ["<-5%", "-5% to -2%", "-2% to -1%", "-1% to 0%", "0% to 1%", "1% to 2%", "2% to 5%", ">5%"]
    cats = pd.cut(pnl, bins=bins, labels=labels)
    counts = cats.value_counts(sort=False).to_dict()

    buckets = [{"label": str(k), "count": int(v)} for k, v in counts.items()]
    return {
        "buckets": buckets,
        "mean": _round_float(pnl.mean(), decimals) or 0.0,
        "median": _round_float(pnl.median(), decimals) or 0.0,
        "std": _round_float(pnl.std(), decimals) or 0.0,
        "skew": _round_float(pnl.skew(), decimals) or 0.0,
        "kurtosis": _round_float(pnl.kurt(), decimals) or 0.0,
    }


def _calc_regime_performance(trades_df: pd.DataFrame, decimals: int) -> list[dict[str, Any]]:
    """Aggregate trade performance by market regime if available."""
    if trades_df.empty:
        return []
    col = "market_regime" if "market_regime" in trades_df.columns else "regime"
    if col not in trades_df.columns:
        return []

    pnl_col = "return_pct" if "return_pct" in trades_df.columns else ("pnl_pct" if "pnl_pct" in trades_df.columns else None)
    out = []
    for regime, group in trades_df.groupby(col):
        n = len(group)
        if n == 0:
            continue
        pnl = group[pnl_col].dropna() if pnl_col else pd.Series(dtype=float)
        wins = sum(1 for p in pnl if p > 0)
        win_rate = round((wins / n) * 100.0, decimals) if n > 0 else 0.0
        mean_ret = round(float(pnl.mean()), decimals) if len(pnl) > 0 else 0.0
        out.append({
            "regime": str(regime),
            "trades": n,
            "win_rate_pct": win_rate,
            "mean_return_pct": mean_ret,
        })
    return out


class StrategyExperimentService:
    """Orchestrates strategy experiments comparing Baseline vs Candidate under identical assumptions."""

    def __init__(
        self,
        storage: StrategyResearchStoragePort,
        historical_feed: HistoricalFeedPort | None = None,
        *,
        curve_max_points: int,
        result_decimals: int,
    ) -> None:
        self._storage = storage
        self._historical_feed = historical_feed
        self._curve_max_points = curve_max_points
        self._result_decimals = result_decimals

    def resolve_feed_for_strategy(
        self,
        *,
        strategy_id: str | None = None,
        strategy_definition: dict[str, Any] | None = None,
        user_id: str,
    ) -> BacktestFeedPort:
        """Load real history for symbols stated by deployment or definition.

        Args:
            strategy_id: Strategy whose deployment settings may name the market.
            strategy_definition: Strategy definition with explicit symbols or universe.
            user_id: Owner used to scope deployment settings.

        Returns:
            A feed built from the requested historical market data.

        Raises:
            RuntimeError: No historical feed adapter was configured.
            ValueError: No explicit symbols or universe were declared.
        """
        if self._historical_feed is None:
            raise RuntimeError("historical feed adapter is not configured")

        selection: dict[str, Any] = {}
        if strategy_id:
            with self._storage.unit_of_work() as work:
                if not work.owns_strategy(strategy_id, user_id):
                    raise LookupError(f"Strategy {strategy_id} not found")
                deployments = work.recent_deployments(strategy_id, user_id)
            for deployment in deployments:
                config = deployment.get("config")
                if isinstance(config, str):
                    config = json.loads(config)
                if isinstance(config, dict) and (config.get("symbols") or config.get("universe")):
                    selection = config
                    break

        definition = strategy_definition or {}
        symbols = selection.get("symbols") or definition.get("symbols") or definition.get("universe_symbols")
        universe = selection.get("universe") or (None if symbols else definition.get("universe"))
        exchange = selection.get("exchange") or definition.get("exchange")
        if not symbols and not universe:
            raise ValueError("strategy must declare symbols or a named universe for historical data")
        return self._historical_feed.load(
            exchange=exchange,
            symbols=symbols,
            universe=universe,
        )

    def create_experiment(
        self,
        strategy_id: str,
        *,
        source_version: int | None = None,
        parameter_changes: dict[str, Any] | None = None,
        creator_user_id: str,
        name: str | None = None,
        reason: str | None = None,
        recommendation_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a new experiment record from strategy version and parameter adjustments."""
        with self._storage.unit_of_work() as work:
            # 1. Resolve strategy version
            if source_version is None:
                latest_ver = work.latest_strategy_version(strategy_id, creator_user_id)
                if latest_ver is None:
                    raise LookupError(f"No version found for strategy {strategy_id}")
                source_ver_row = latest_ver
            else:
                source_ver_row = work.strategy_version(
                    strategy_id, source_version, creator_user_id
                )
                if source_ver_row is None:
                    raise LookupError(f"Strategy {strategy_id} v{source_version} not found")

            src_v = int(source_ver_row["version"])
            raw_def = source_ver_row["definition"]
            baseline_def = json.loads(raw_def) if isinstance(raw_def, str) else dict(raw_def)

            # 2. Check recommendation if provided
            rec_row = None
            if recommendation_id:
                rec_row = work.recommendation(recommendation_id, creator_user_id)
                if rec_row and not reason:
                    reason = f"Derived from recommendation {recommendation_id}: {rec_row.get('reason')}"

            # If parameter_changes not passed, infer from recommendation
            if not parameter_changes and rec_row:
                parameter_changes = {rec_row["parameter"]: rec_row["proposed_value"]}
            elif not parameter_changes:
                parameter_changes = {}

            # 3. Apply parameter changes to candidate definition and normalize parameter_changes dict
            candidate_def = json.loads(json.dumps(baseline_def))
            adaptive_specs = extract_adaptive_parameters(baseline_def)
            normalized_changes: dict[str, Any] = {}

            for p_name, p_val in parameter_changes.items():
                adaptive_spec = adaptive_specs.get(p_name)
                target_block = adaptive_spec.block if adaptive_spec else None
                _, baseline_values = locate_parameter(baseline_def, p_name, target_block)
                base_val = baseline_values[p_name]
                if isinstance(p_val, dict) and "candidate" in p_val:
                    cand_val = p_val["candidate"]
                    supplied_baseline = p_val.get("baseline")
                elif isinstance(p_val, dict) and "proposed" in p_val:
                    cand_val = p_val["proposed"]
                    supplied_baseline = p_val.get("current")
                else:
                    cand_val = p_val
                    supplied_baseline = None
                if supplied_baseline is not None and supplied_baseline != base_val:
                    raise ValueError(
                        f"parameter {p_name!r} baseline does not match the strategy definition"
                    )

                candidate_def = apply_parameter_to_definition(
                    candidate_def,
                    p_name,
                    cand_val,
                    block=target_block,
                )
                normalized_changes[p_name] = {
                    "baseline": base_val,
                    "candidate": cand_val,
                }

            exp_name = name or f"Experiment: {', '.join(normalized_changes.keys())} on v{src_v}"
            exp_reason = reason or f"Comparative evaluation of {list(normalized_changes.keys())} against v{src_v}"

            return work.create_experiment(
                creator_user_id,
                strategy_id=strategy_id,
                source_version=src_v,
                creator_user_id=creator_user_id,
                name=exp_name,
                reason=exp_reason,
                parameter_changes=normalized_changes,
                baseline_definition=baseline_def,
                candidate_definition=candidate_def,
                recommendation_id=recommendation_id,
            )

    def run_experiment(
        self,
        experiment_id: str,
        *,
        user_id: str,
        feed: BacktestFeedPort | None = None,
        config: BacktestConfig | None = None,
        wf_config: WalkForwardConfig | None = None,
    ) -> dict[str, Any]:
        """Execute experiment: runs Baseline and Candidate under IDENTICAL assumptions.

        Calculates:
        - Side-by-side metric comparison table
        - Equity and Drawdown curves
        - Monthly return matrix
        - Return distributions (skew, kurtosis, buckets)
        - Regime performance
        - Walk-forward out-of-sample validation
        - Parameter robustness analysis
        - Structured explanation: WHAT CHANGED, WHY PROPOSED, WHAT EXPERIMENT FOUND
        """
        with self._storage.unit_of_work() as work:
            exp = work.experiment(experiment_id, user_id)
            if not exp:
                raise LookupError(f"Experiment {experiment_id} not found")
            work.update_experiment_status(experiment_id, user_id, "RUNNING")

        baseline_def = exp["baseline_definition"]
        candidate_def = exp["candidate_definition"]
        strategy_id = exp.get("strategy_id")

        eval_feed = feed if feed is not None else self.resolve_feed_for_strategy(
            strategy_id=strategy_id,
            strategy_definition=baseline_def,
            user_id=user_id,
        )
        bt_config = config if config is not None else BacktestConfig()

        try:
            # 1. Evaluate Baseline Backtest
            base_res, base_m = evaluate_backtest(eval_feed, baseline_def, bt_config)

            # 2. Evaluate Candidate Backtest under exact identical assumptions
            cand_res, cand_m = evaluate_backtest(eval_feed, candidate_def, bt_config)

            # 3. Walk-Forward OOS Validation on candidate
            param_changes = exp.get("parameter_changes") or {}
            first_param = next(iter(param_changes.keys()), "param")
            val_entry = param_changes.get(first_param, {})
            if isinstance(val_entry, dict):
                prop_val = val_entry.get("candidate", val_entry.get("proposed", 0.0))
            else:
                prop_val = val_entry

            # Use an adaptive walk-forward configuration based on available history length
            snapshots = eval_feed.load()
            total_bars = len(snapshots)
            if wf_config is not None:
                effective_wf_config = wf_config
            elif total_bars >= 400:
                effective_wf_config = WalkForwardConfig(train_bars=252, test_bars=63, step_bars=63, warmup_bars=60)
            elif total_bars >= 180:
                effective_wf_config = WalkForwardConfig(train_bars=120, test_bars=30, step_bars=30, warmup_bars=30)
            else:
                effective_wf_config = WalkForwardConfig(train_bars=60, test_bars=20, step_bars=20, warmup_bars=10)

            wf_m = evaluate_walk_forward(
                eval_feed,
                baseline_def,
                first_param,
                float(prop_val),
                wf_config=effective_wf_config,
                bt_config=bt_config,
            )

            # 4. Robustness scan
            adaptive_specs = extract_adaptive_parameters(baseline_def)
            param_spec = adaptive_specs.get(first_param)
            if param_spec:
                rob = evaluate_robustness(
                    eval_feed,
                    baseline_def,
                    param_spec,
                    float(prop_val),
                    config=bt_config,
                )
            else:
                rob = RobustnessResult(
                    parameter=first_param,
                    tested_values=[float(prop_val)],
                    metrics_by_value={str(prop_val): cand_m.to_dict()},
                    is_stable=True,
                    is_isolated_spike=False,
                    stable_range=(float(prop_val), float(prop_val)),
                    sensitivity_verdict="Single parameter point evaluated (unbounded)",
                )

            # 5. Build Side-by-Side Metric Comparison Table
            metric_keys = [
                ("total_return_pct", "Return (%)", "%", True),
                ("max_drawdown_pct", "Max Drawdown (%)", "%", False),
                ("sharpe", "Sharpe Ratio", "", True),
                ("sortino", "Sortino Ratio", "", True),
                ("profit_factor", "Profit Factor", "", True),
                ("win_rate_pct", "Win Rate (%)", "%", True),
                ("num_trades", "Trades Count", "", True),
                ("median_return", "Median Trade (%)", "%", True),
                ("cagr_pct", "CAGR (%)", "%", True),
            ]

            metrics_table = []
            for key, label, unit, higher_is_better in metric_keys:
                cur_val = getattr(base_m, key, None)
                cand_val = getattr(cand_m, key, None)
                diff = None
                if cur_val is not None and cand_val is not None:
                    diff = round(float(cand_val) - float(cur_val), self._result_decimals)
                metrics_table.append({
                    "metric": key,
                    "label": label,
                    "unit": unit,
                    "current": cur_val,
                    "candidate": cand_val,
                    "difference": diff,
                    "higher_is_better": higher_is_better,
                })

            # Add OOS Efficiency metric
            metrics_table.append({
                "metric": "oos_efficiency",
                "label": "OOS Efficiency Ratio",
                "unit": "",
                "current": 1.0 if wf_m.in_sample_sharpe > 0 else None,
                "candidate": wf_m.efficiency_ratio,
                "difference": round(wf_m.efficiency_ratio - 1.0, self._result_decimals) if wf_m.in_sample_sharpe > 0 else None,
                "higher_is_better": True,
            })

            # 6. Curves & Distributions
            base_equity = base_res.equity
            cand_equity = cand_res.equity

            equity_curves = {
                "baseline": _calc_curve_points(base_equity, self._curve_max_points, self._result_decimals),
                "candidate": _calc_curve_points(cand_equity, self._curve_max_points, self._result_decimals),
            }

            drawdown_curves = {
                "baseline": _calc_curve_points(_calc_drawdown_curve(base_equity), self._curve_max_points, self._result_decimals),
                "candidate": _calc_curve_points(_calc_drawdown_curve(cand_equity), self._curve_max_points, self._result_decimals),
            }

            monthly_returns = {
                "baseline": _calc_monthly_returns(base_equity, self._result_decimals),
                "candidate": _calc_monthly_returns(cand_equity, self._result_decimals),
            }

            return_distributions = {
                "baseline": _calc_return_distribution(base_res.trades, self._result_decimals),
                "candidate": _calc_return_distribution(cand_res.trades, self._result_decimals),
            }

            regime_performance = {
                "baseline": _calc_regime_performance(base_res.trades, self._result_decimals),
                "candidate": _calc_regime_performance(cand_res.trades, self._result_decimals),
            }

            # 7. Formulate Honest Structured Explanation
            # WHAT CHANGED
            changes_desc = []
            for p, v in param_changes.items():
                if isinstance(v, dict):
                    base_v = v.get("baseline", v.get("current", "—"))
                    cand_v = v.get("candidate", v.get("proposed", "—"))
                    changes_desc.append(f"{p}: {base_v} → {cand_v}")
                else:
                    changes_desc.append(f"{p}: → {v}")
            what_changed = ", ".join(changes_desc)

            # WHY IT WAS PROPOSED
            why_proposed = exp.get("reason") or "Identified via forward statistical pattern detection & research candidate."

            # WHAT THE EXPERIMENT FOUND
            what_found = (
                f"Backtest: PF {base_m.profit_factor:.2f} → {cand_m.profit_factor:.2f}, "
                f"Sharpe {base_m.sharpe:.2f} → {cand_m.sharpe:.2f}, MaxDD {base_m.max_drawdown_pct:.1f}% → {cand_m.max_drawdown_pct:.1f}%. "
                f"Walk-Forward OOS: {wf_m.verdict}. "
                f"Robustness: {rob.sensitivity_verdict}."
            )

            explanation = {
                "what_changed": what_changed,
                "why_proposed": why_proposed,
                "what_experiment_found": what_found,
            }

            results_payload = {
                "metrics_table": metrics_table,
                "baseline_metrics": base_m.to_dict(),
                "candidate_metrics": cand_m.to_dict(),
                "walk_forward_metrics": wf_m.to_dict(),
                "robustness_results": rob.to_dict(),
                "equity_curves": equity_curves,
                "drawdown_curves": drawdown_curves,
                "monthly_returns": monthly_returns,
                "return_distributions": return_distributions,
                "regime_performance": regime_performance,
            }

            with self._storage.unit_of_work() as work:
                updated = work.save_experiment_results(
                    experiment_id,
                    user_id,
                    results=results_payload,
                    explanation=explanation,
                    status="COMPLETED",
                )
            if updated is None:
                raise LookupError(f"Experiment {experiment_id} not found")
            return updated

        except Exception as exc:
            logger.exception("Experiment execution failed: {}", exc)
            with self._storage.unit_of_work() as work:
                work.update_experiment_status(
                    experiment_id,
                    user_id,
                    "FAILED",
                    error=str(exc),
                )
            raise

    def run_friction_counterfactual(
        self,
        strategy_id: str,
        version: int | None = None,
        *,
        creator_user_id: str,
        multiplier: float = 1.5,
        feed: BacktestFeedPort | None = None,
    ) -> dict[str, Any]:
        """Turn a banked cost finding into a run experiment.

        A ``cost_drag:cost_drowned`` finding says friction ate the move; the
        counterfactual worth testing is a wider stop, not a tighter one (the
        generic candidate generator steps *with* a negative lift, which would
        tighten stops into even more friction). This proposes
        ``stop_loss_pct × multiplier`` on the finding's strategy, runs it
        through the standard baseline-vs-candidate evaluation, and returns
        the record — or a stated reason when there is nothing to test.

        Not-created is a return value, not an exception: no cost finding, or
        no stop parameter in the definition, are normal states, not errors.
        """
        with self._storage.unit_of_work() as work:
            observations = work.learning_observations(strategy_id, creator_user_id)
        cost_findings = [
            o for o in observations
            if str(o.get("condition_bucket") or "").startswith("cost_drag:")
        ]
        if not cost_findings:
            return {
                "created": False,
                "reason": (
                    f"no banked cost finding for strategy {strategy_id}; "
                    "counterfactuals need a recorded friction lesson first"
                ),
            }
        finding = max(cost_findings, key=lambda o: int(o.get("sample_size") or 0))

        with self._storage.unit_of_work() as work:
            ver_row = work.strategy_version_record(
                strategy_id, version, creator_user_id
            )
            if ver_row is None:
                return {
                    "created": False,
                    "reason": f"strategy {strategy_id} v{version} not found",
                }
            src_v = int(ver_row["version"])
            raw_def = ver_row["definition"]
        definition = json.loads(raw_def) if isinstance(raw_def, str) else dict(raw_def)

        current: Any = (definition.get("params") or {}).get("stop_loss_pct")
        if current is None:
            exit_block = (definition.get("rules") or {}).get("exit") or {}
            current = exit_block.get("stop_loss_pct")
        if current is None:
            return {
                "created": False,
                "reason": (
                    f"strategy {strategy_id} v{src_v} declares no stop_loss_pct; "
                    "a wider-stop counterfactual has nothing to widen"
                ),
            }
        try:
            current_f = float(current)
        except (TypeError, ValueError):
            return {
                "created": False,
                "reason": f"stop_loss_pct {current!r} is not numeric",
            }
        candidate = round(current_f * multiplier, 4)

        bucket = finding.get("condition_bucket")
        sample_size = finding.get("sample_size")
        reason = (
            f"Banked finding {bucket} (n={sample_size}): costs drowned the move. "
            f"Counterfactual: stop_loss_pct {current_f} → {candidate} "
            f"({multiplier}x), run against the identical feed to see whether "
            "fewer, wider stops keep more of the move than they give back."
        )
        exp = self.create_experiment(
            strategy_id,
            source_version=src_v,
            parameter_changes={
                "stop_loss_pct": {"baseline": current_f, "candidate": candidate}
            },
            creator_user_id=creator_user_id,
            name=f"Friction counterfactual: stop {current_f} → {candidate} on v{src_v}",
            reason=reason,
        )
        result = self.run_experiment(
            exp["experiment_id"], user_id=creator_user_id, feed=feed
        )
        return {
            "created": True,
            "experiment_id": exp["experiment_id"],
            "strategy_id": strategy_id,
            "source_version": src_v,
            "parameter": "stop_loss_pct",
            "baseline": current_f,
            "candidate": candidate,
            "finding_bucket": bucket,
            "finding_sample_size": sample_size,
            "result": result,
        }

    def approve_experiment(self, experiment_id: str, user_id: str) -> dict[str, Any]:
        """Operator explicitly marks an experiment as APPROVED."""
        with self._storage.unit_of_work() as work:
            exp = work.experiment(experiment_id, user_id)
            if not exp:
                raise LookupError(f"Experiment {experiment_id} not found")
            if exp["status"] not in ("COMPLETED", "REJECTED"):
                raise ValueError(f"Cannot approve experiment with status {exp['status']}")

            res = work.update_experiment_status(
                experiment_id,
                user_id,
                "APPROVED",
                reviewed_by=user_id,
            )
            if res is None:
                raise LookupError(f"Experiment {experiment_id} not found")
            return res

    def reject_experiment(self, experiment_id: str, user_id: str, reason: str | None = None) -> dict[str, Any]:
        """Operator rejects an experiment."""
        with self._storage.unit_of_work() as work:
            exp = work.experiment(experiment_id, user_id)
            if not exp:
                raise LookupError(f"Experiment {experiment_id} not found")
            if exp["status"] == "APPLIED":
                raise ValueError("Cannot reject an already applied experiment")

            res = work.update_experiment_status(
                experiment_id,
                user_id,
                "REJECTED",
                reviewed_by=user_id,
                rejection_reason=reason or "Operator rejected experimental candidate.",
            )
            if res is None:
                raise LookupError(f"Experiment {experiment_id} not found")
            return res

    def apply_experiment(self, experiment_id: str, user_id: str) -> dict[str, Any]:
        """Apply an approved experiment as a new immutable version.

        Args:
            experiment_id: Approved experiment to apply.
            user_id: Account that authors the new version.

        Returns:
            The updated experiment and the new strategy version.

        Raises:
            LookupError: The experiment or its source version is missing.
            ValueError: The experiment is not approved or has no candidate definition.
        """
        with self._storage.unit_of_work() as work:
            experiment = work.experiment(experiment_id, user_id)
            if experiment is None:
                raise LookupError(f"no experiment {experiment_id}")
            if experiment["status"] != "APPROVED":
                raise ValueError(
                    f"cannot apply experiment with status {experiment['status']}; "
                    "must be APPROVED"
                )

            strategy_id = experiment["strategy_id"]
            source_version_number = experiment["source_version"]
            source_version = work.strategy_version(
                strategy_id, source_version_number, user_id
            )
            if source_version is None:
                raise LookupError(
                    f"no source strategy version {strategy_id} v{source_version_number}"
                )

            candidate_definition = experiment.get("candidate_definition")
            if not candidate_definition or not isinstance(candidate_definition, dict):
                raise ValueError("experiment lacks valid candidate definition")

            change_note = (
                f"Applied Experiment Lab result '{experiment['name']}' "
                f"(experiment {experiment_id}): "
                f"{json.dumps(experiment.get('parameter_changes') or {})}"
            )
            new_version = work.create_strategy_version(
                strategy_id=strategy_id,
                author_user_id=user_id,
                definition=candidate_definition,
                change_note=change_note,
            )
            updated = work.update_experiment_status(
                experiment_id,
                user_id,
                "APPLIED",
                reviewed_by=user_id,
                target_version=new_version["version"],
            )
            if updated is None:
                raise LookupError(f"Experiment {experiment_id} not found")
            return {"experiment": updated, "new_version": new_version}

    def list_experiments(
        self,
        user_id: str,
        strategy_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List experiments for auditing and history."""
        with self._storage.unit_of_work() as work:
            if strategy_id and not work.owns_strategy(strategy_id, user_id):
                raise LookupError(f"Strategy {strategy_id} not found")
            return work.experiments(
                user_id,
                strategy_id=strategy_id,
                status=status,
                limit=limit,
            )

    def get_experiment(
        self, experiment_id: str, user_id: str
    ) -> dict[str, Any] | None:
        """Fetch detailed experiment report."""
        with self._storage.unit_of_work() as work:
            return work.experiment(experiment_id, user_id)
