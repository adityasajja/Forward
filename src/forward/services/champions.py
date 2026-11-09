"""Champion vs challenger operations: launch paired arms, compare their evidence.

Two halves with opposite safety properties:

* :meth:`ChampionService.launch_challenger` **writes** — it creates one PAPER
  deployment. It delegates to :meth:`DeploymentService.launch_challenger`,
  which owns every refusal, so this method adds no lifecycle of its own.
* :meth:`ChampionService.compare` **only reads** — dataset rows, recorded
  contexts, versions, deployments. It assembles a side-by-side view and a
  readiness verdict. It cannot promote, pause, edit or deploy anything, and
  there is deliberately no method here that could: promotion is a human
  decision taken outside this system.

Both arms' trades already feed the learning system separately — journal
episodes and dataset rows carry ``strategy_version``, and the comparison
selects each arm by exact version, never by recency or by name.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from forward.core.portfolio_ports import (
    ChampionStoragePort,
)
from forward.research.champion_challenger import (
    compare_arms,
    definition_diff,
    version_timeline,
)

if TYPE_CHECKING:
    from forward.services.deployments import DeploymentService
    from forward.services.learning import LearningService
    from forward.signal_context.service import SignalContextService


class ChampionService:
    """Paired PAPER arms for one strategy: launch, then compare."""

    def __init__(
        self,
        storage: ChampionStoragePort,
        learning: LearningService,
        contexts: SignalContextService,
        deployments: DeploymentService,
    ) -> None:
        self._storage = storage
        self._learning = learning
        self._contexts = contexts
        self._deployments = deployments

    # ------------------------------------------------------------------ launch
    def launch_challenger(
        self,
        user_id: str,
        *,
        strategy_id: str,
        champion_deployment_id: str,
        challenger_version: int,
    ) -> dict[str, Any]:
        """Create the challenger arm against a running champion.

        A thin pass-through: every check and refusal lives in
        :meth:`DeploymentService.launch_challenger`, which is also what the
        API route calls, so the two paths cannot disagree.
        """
        return self._deployments.launch_challenger(
            user_id,
            strategy_id=strategy_id,
            champion_deployment_id=champion_deployment_id,
            challenger_version=int(challenger_version),
        )

    # ----------------------------------------------------------------- compare
    def compare(
        self,
        user_id: str,
        strategy_id: str,
        *,
        champion_version: int | None = None,
        challenger_version: int | None = None,
        refresh: bool = False,
    ) -> dict[str, Any]:
        """Side-by-side forward evidence for two versions. Read-only.

        Roles are explicit when given; otherwise the oldest and newest RUNNING
        PAPER versions become champion and challenger. Anything else — fewer
        than two running arms, an unknown version — is a refused comparison,
        not a guessed one.
        """
        from forward.research import learning_readiness as lr
        from forward.services.deployments import DeploymentError
        from forward.services.learning_analysis import resolve_metric
        from forward.services.learning_dataset_model import LearningDataset

        champion_v, challenger_v = self._resolve_roles(
            user_id,
            strategy_id,
            champion_version=champion_version,
            challenger_version=challenger_version,
        )

        versions = self._storage.strategy_versions(user_id, strategy_id)
        definitions = {
            int(version["version"]): _parse_definition(version.get("definition"))
            for version in versions
        }
        running = self._storage.running_paper_deployments(user_id, strategy_id)
        if champion_v not in definitions:
            raise DeploymentError(
                f"strategy {strategy_id} has no version {champion_v}",
                code="version_not_found",
                status=422,
            )
        if challenger_v not in definitions:
            raise DeploymentError(
                f"strategy {strategy_id} has no version {challenger_v}",
                code="version_not_found",
                status=422,
            )

        dataset = self._learning.dataset(refresh=refresh, user_id=user_id)
        closed_forward = lr.forward_closed(dataset.rows)
        unique, dropped = lr.dedupe_forward(closed_forward)

        def _arm(version: int) -> list[dict[str, Any]]:
            return [
                r
                for r in unique
                if str(r.get("strategy_id") or "") == str(strategy_id)
                and r.get("strategy_version") is not None
                and int(r.get("strategy_version")) == int(version)
            ]

        champion_rows, challenger_rows = _arm(champion_v), _arm(challenger_v)
        combined = LearningDataset(
            rows=champion_rows + challenger_rows,
            missing_features={},
            generated_at=dataset.generated_at,
        )
        metric, metric_note = resolve_metric(combined, None)
        comparison = compare_arms(champion_rows, challenger_rows, metric=metric)

        context = {
            "champion": self._arm_context(user_id, strategy_id, champion_v),
            "challenger": self._arm_context(user_id, strategy_id, challenger_v),
        }

        arm_deployments = {
            "champion": _deployment_view(running, champion_v),
            "challenger": _deployment_view(running, challenger_v),
        }
        parity = _parity(arm_deployments["champion"], arm_deployments["challenger"])

        counts: dict[Any, int] = {}
        for row in unique:
            if str(row.get("strategy_id") or "") != str(strategy_id):
                continue
            version = row.get("strategy_version")
            if version is None:
                continue
            counts[int(version)] = counts.get(int(version), 0) + 1
        timeline = version_timeline(
            versions,
            champion_version=champion_v,
            challenger_version=challenger_v,
            forward_counts=counts,
        )

        return {
            "strategy_id": strategy_id,
            "champion_version": champion_v,
            "challenger_version": challenger_v,
            "generated_at": dataset.summary()["generated_at"],
            "metric": metric,
            "metric_note": metric_note,
            "duplicates_dropped": len(dropped),
            "comparison": comparison,
            "context": context,
            "deployments": arm_deployments,
            "parity": parity,
            "definition_diff": definition_diff(definitions[champion_v], definitions[challenger_v]),
            "timeline": timeline,
            # Stated so no client can render a verdict as an action taken.
            "advisory_only": True,
            "applies_changes": False,
            "promotes": False,
        }

    # ------------------------------------------------------------------ helpers
    def _resolve_roles(
        self,
        user_id: str,
        strategy_id: str,
        *,
        champion_version: int | None,
        challenger_version: int | None,
    ) -> tuple[int, int]:
        from forward.services.deployments import DeploymentError

        if champion_version is not None and challenger_version is not None:
            if int(champion_version) == int(challenger_version):
                raise DeploymentError(
                    "champion and challenger must be different versions; "
                    "comparing a version with itself is always a tie",
                    code="same_version",
                    status=422,
                )
            return int(champion_version), int(challenger_version)

        running = [
            row
            for row in self._storage.running_paper_deployments(user_id, strategy_id)
            if row.get("strategy_version") is not None
        ]
        armed = sorted({int(r["strategy_version"]) for r in running})
        if len(armed) < 2:
            raise DeploymentError(
                f"strategy {strategy_id} has {len(armed)} running PAPER version(s); "
                "a champion-vs-challenger comparison needs two — name them "
                "explicitly or launch a challenger first",
                code="needs_two_arms",
                status=422,
            )
        return armed[0], armed[-1]

    def _arm_context(self, user_id: str, strategy_id: str, version: int) -> dict[str, Any]:
        """Per-arm context-score performance, compacted for the dashboard.

        Args:
            user_id: Account that owns the strategy.
            strategy_id: Strategy associated with the evidence.
            version: Exact strategy version for this arm.

        Returns:
            Context evidence needed by the comparison dashboard.
        """
        result = self._contexts.effectiveness(
            user_id,
            strategy_id=strategy_id,
            strategy_version=int(version),
            source="PAPER",
        )
        verdict = result.get("score_verdict") or {}
        return {
            "forward_n": result.get("forward_n", 0),
            "forward_with_metric": result.get("forward_with_metric", 0),
            "bands": dict(verdict.get("means") or {}),
            "monotonic_high_is_better": verdict.get("monotonic_high_is_better"),
            "may_claim": verdict.get("may_claim", False),
            "statement": verdict.get("statement", ""),
        }


def _parse_definition(raw: Any) -> Any:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        return json.loads(raw)
    return raw


def _deployment_view(running: list[dict[str, Any]], version: int) -> dict[str, Any] | None:
    for row in running:
        if row.get("strategy_version") is not None and int(row["strategy_version"]) == int(version):
            return {
                "deployment_id": row.get("deployment_id"),
                "mode": row.get("mode"),
                "status": row.get("status"),
                "capital": row.get("capital"),
                "config": _parse_definition(row.get("config")),
            }
    return None


def _parity(champion: dict[str, Any] | None, challenger: dict[str, Any] | None) -> dict[str, Any]:
    """Do the two arms actually run under identical conditions? Stated, not assumed."""
    mismatches: list[str] = []
    if champion is None or challenger is None:
        missing = "champion" if champion is None else "challenger"
        mismatches.append(f"no RUNNING PAPER deployment found for the {missing} arm")
        return {
            "identical_capital": False,
            "identical_config": False,
            "venue_shared": True,
            "mismatches": mismatches,
            "note": (
                "market data, trading costs and the slippage model are shared "
                "structurally either way: both loops fill against the runner's "
                "single venue."
            ),
        }
    identical_capital = champion.get("capital") == challenger.get("capital")
    if not identical_capital:
        mismatches.append(
            f"capital differs: {champion.get('capital')} vs {challenger.get('capital')}"
        )
    identical_config = champion.get("config") == challenger.get("config")
    if not identical_config:
        mismatches.append("deployment configs differ: universes or sizing may not match")
    return {
        "identical_capital": identical_capital,
        "identical_config": identical_config,
        "venue_shared": True,
        "mismatches": mismatches,
        "note": (
            "market data, trading costs and the slippage model are shared "
            "structurally: both loops fill against the runner's single venue."
        ),
    }
