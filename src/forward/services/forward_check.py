"""Is the strategy doing in paper trading what its backtest said it would?

Compared per trade, not per period, so a six-week paper run can be set beside a six-year backtest:
how often it wins, what an average trade returns, and how much it makes per rupee it loses. Too few
paper trades means "too early", never a verdict.
"""

from __future__ import annotations

import math
from typing import Any

from forward.config.settings import Settings, get_settings
from forward.core.forward_tracking_ports import (
    ForwardCheckStoragePort,
)
from forward.services.forward_trade_evidence import (
    is_forward_trade,
    matches_strategy_version,
)


def _finite_number(value: Any) -> float | None:
    """Return a finite numeric value, or ``None`` when it is invalid.

    Args:
        value: Value read from a stored trade row.

    Returns:
        A finite float, or ``None`` when the value cannot be used.
    """
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _stats(returns: list[float], pnls: list[float]) -> dict[str, Any]:
    n = len(returns)
    if n == 0:
        return {"trades": 0, "win_rate_pct": None, "avg_trade_pct": None, "profit_factor": None}
    wins = sum(1 for p in pnls if p > 0)
    gain = sum(p for p in pnls if p > 0)
    loss = -sum(p for p in pnls if p < 0)
    return {
        "trades": n,
        "win_rate_pct": round(wins / n * 100, 1),
        "avg_trade_pct": round(sum(returns) / n, 2),
        "profit_factor": round(gain / loss, 2) if loss > 1e-9 else None,
    }


def verdict(
    backtest: dict[str, Any], forward: dict[str, Any], *, settings: Settings
) -> tuple[str, str]:
    """Compare paper results with the completed backtest results.

    Args:
        backtest: Statistics for backtest trades with complete P&L.
        forward: Statistics for forward trades with complete P&L.
        settings: Required service policy values.

    Returns:
        A result code and a short explanation.
    """
    if backtest["trades"] == 0:
        return "no_backtest", "No backtest to compare with."
    if forward["trades"] < settings.forward_check_min_trades:
        return "too_early", (
            f"Too early: {forward['trades']} of "
            f"{settings.forward_check_min_trades} trades."
        )
    bt_avg, fw_avg = backtest["avg_trade_pct"], forward["avg_trade_pct"]
    win_gap = float(backtest["win_rate_pct"]) - float(forward["win_rate_pct"])
    keeps_edge = fw_avg > 0 and (
        bt_avg <= 0 or fw_avg >= settings.forward_check_keep_share * bt_avg
    )
    if keeps_edge and win_gap <= settings.forward_check_win_tolerance_points:
        return "holding_up", "Matching the backtest."
    return "weaker", "Weaker than the backtest."


class ForwardCheckService:
    """Compare backtest results with closed paper and live trades."""

    def __init__(self, storage: ForwardCheckStoragePort) -> None:
        """Create the comparison use case.

        Args:
            storage: Port used to read backtest and forward trades.
        """
        self._storage = storage
        self._settings = get_settings()

    def compare(self, user_id: str, strategy_id: str, version: int | None = None) -> dict[str, Any]:
        """Compare trade returns for one strategy version.

        Args:
            user_id: Account that owns the strategy and trades.
            strategy_id: Strategy being checked.
            version: Exact strategy version, or ``None`` for any version.

        Returns:
            Backtest and forward statistics with the comparison verdict.
        """
        run_id, backtest_rows = self._storage.latest_backtest_trades(user_id, strategy_id, version)
        bt_returns: list[float] = []
        bt_pnls: list[float] = []
        bt_incomplete = 0
        for row in backtest_rows:
            ret = _finite_number(row.get("return_pct"))
            pnl = _finite_number(row.get("net_pnl"))
            if ret is None or pnl is None:
                bt_incomplete += 1
                continue
            bt_returns.append(ret)
            bt_pnls.append(pnl)

        fw_returns: list[float] = []
        fw_pnls: list[float] = []
        fw_incomplete = 0
        for row in self._forward_rows(user_id, strategy_id, version):
            if row.get("exit_ts") is None:
                continue
            entry_price = _finite_number(row.get("entry_price"))
            quantity = _finite_number(row.get("quantity"))
            pnl = _finite_number(row.get("net_pnl"))
            if entry_price is None or quantity is None or pnl is None:
                fw_incomplete += 1
                continue
            cost = entry_price * quantity
            if cost <= 0:
                fw_incomplete += 1
                continue
            fw_returns.append(pnl / cost * 100.0)
            fw_pnls.append(pnl)

        backtest, forward = _stats(bt_returns, bt_pnls), _stats(fw_returns, fw_pnls)
        code, message = verdict(backtest, forward, settings=self._settings)
        return {
            "strategy_id": strategy_id,
            "version": version,
            "backtest": {
                **backtest,
                "run_id": run_id,
                "excluded_incomplete_trades": bt_incomplete,
            },
            "paper": {**forward, "excluded_incomplete_trades": fw_incomplete},
            "verdict": code,
            "message": message,
            "min_paper_trades": self._settings.forward_check_min_trades,
        }

    def _forward_rows(
        self, user_id: str, strategy_id: str, version: int | None
    ) -> list[dict[str, Any]]:
        """Select forward evidence rows with the shared gating rules.

        Args:
            user_id: Account that owns the journal rows.
            strategy_id: Strategy whose rows are requested.
            version: Exact strategy version, or ``None`` for any version.

        Returns:
            Rows marked as paper or live forward evidence.
        """
        result = []
        for row in self._storage.forward_trades(user_id, strategy_id):
            if not matches_strategy_version(row, version) or not is_forward_trade(row):
                continue
            result.append(row)
        return result
