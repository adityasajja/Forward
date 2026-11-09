"""Readiness gates that decide, not just describe.

:mod:`forward.research.learning_readiness` deliberately stops at research labels —
it will never pause, cap, or refuse anything. Something has to act on those
labels, or "reliable in real decisions" is a slogan. This module is that
something, and it is deliberately narrow:

* PAPER deployments are always allowed. Evidence must come from somewhere;
  refusing first runs would make learning impossible by construction.
* LIVE deployments require fifty *effective* forward trades
  (``ANALYSIS_READY_N``), counted with :func:`effective_n` so one crowded
  minute cannot clear the gate. A refusal states the current effective
  sample, the cluster count, and the next gate.
* Dry-run is the default. :func:`evaluate_gate` decides without touching
  anything; only an explicit enforcement call acts.

Effective sample, not raw rows: fourteen fills in one minute banked zero
findings under Phase 2 for exactly this reason, and the gate must not be
more gullible than the findings loop.
"""

from __future__ import annotations

from typing import Any

from forward.core.forward_tracking_ports import (
    ForwardTradeStoragePort,
)
from forward.research.learning_readiness import ANALYSIS_READY_N, next_gate
from forward.research.learning_stats import MIN_SAMPLE, effective_n
from forward.services.forward_trade_evidence import (
    is_forward_trade,
    matches_strategy_version,
)


def _forward_rows(
    strategy_id: str,
    version: int | None,
    *,
    user_id: str,
    storage: ForwardTradeStoragePort,
) -> list[dict[str, Any]]:
    """Return forward journal rows that match the account and strategy version.

    Args:
        strategy_id: Strategy whose forward rows are requested.
        version: Exact strategy version, or ``None`` for any version.
        user_id: Account that owns the rows.
        storage: Port that reads forward journal rows.

    Returns:
        Rows marked as paper or live forward evidence.
    """
    rows = storage.forward_trades(user_id, strategy_id)
    return [row for row in rows if matches_strategy_version(row, version) and is_forward_trade(row)]


def evaluate_gate(
    strategy_id: str,
    version: int | None = None,
    *,
    user_id: str,
    storage: ForwardTradeStoragePort,
) -> dict[str, Any]:
    """Decide, without touching anything, whether a strategy may escalate.

    Args:
        strategy_id: Strategy being considered for a live deployment.
        version: Exact strategy version, or ``None`` for any version.
        user_id: Account that owns the rows.
        storage: Port that reads forward journal rows.

    Returns:
        An ``allow`` or ``hold`` action with the evidence counts and reason.
        No rows is ``UNEVALUATED`` and does not block a first paper run.
    """
    rows = _forward_rows(strategy_id, version, user_id=user_id, storage=storage)
    stamps = [row.get("entry_ts") for row in rows]
    n_eff, clusters = effective_n(stamps)
    if not rows:
        return {
            "strategy_id": strategy_id,
            "version": version,
            "action": "allow",
            "state": "UNEVALUATED",
            "n": 0,
            "n_effective": 0.0,
            "clusters": 0,
            "reason": "no forward trades yet; there is nothing to judge",
        }
    if n_eff >= ANALYSIS_READY_N:
        return {
            "strategy_id": strategy_id,
            "version": version,
            "action": "allow",
            "state": "ANALYSIS READY",
            "n": len(rows),
            "n_effective": round(n_eff, 2),
            "clusters": clusters,
            "reason": (
                f"{n_eff:.1f} effective forward trades clear the {ANALYSIS_READY_N}-trade floor"
            ),
        }
    nxt = next_gate(int(n_eff))
    return {
        "strategy_id": strategy_id,
        "version": version,
        "action": "hold",
        "state": "MINIMUM SAMPLE" if n_eff >= MIN_SAMPLE else "NOT READY",
        "n": len(rows),
        "n_effective": round(n_eff, 2),
        "clusters": clusters,
        "next_gate": nxt,
        "reason": (
            f"{len(rows)} forward trades in {clusters} time "
            f"{'cluster' if clusters == 1 else 'clusters'}: effective sample "
            f"{n_eff:.1f} has not cleared the {ANALYSIS_READY_N}-trade floor"
            + (f" (next gate: {nxt})" if nxt else "")
        ),
    }


def check_live_deployment(
    strategy_id: str,
    version: int,
    *,
    user_id: str,
    storage: ForwardTradeStoragePort,
) -> dict[str, Any]:
    """Enforce the gate when creating a LIVE deployment.

    Args:
        strategy_id: Strategy being considered for a live deployment.
        version: Exact strategy version to check.
        user_id: Account that owns the rows.
        storage: Port that reads forward journal rows.

    Returns:
        The ``allow`` decision and the evidence counts behind it.

    Raises:
        DeploymentError: If the evidence does not meet the live threshold.
    """
    from forward.services.deployments import DeploymentError

    decision = evaluate_gate(strategy_id, version, user_id=user_id, storage=storage)
    if decision["action"] == "allow":
        return decision
    raise DeploymentError(
        f"strategy {strategy_id} v{version} holds at {decision['state']}: "
        f"{decision['reason']}. Accrue {ANALYSIS_READY_N} effective forward "
        "trades on paper first.",
        code="unproven_strategy",
        status=422,
    )
