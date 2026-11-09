"""Create and manage paper strategy deployments."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from forward.core.forward_tracking_ports import (
    ForwardTradeStoragePort,
)
from forward.core.paper_deployment_ports import PaperDeploymentStoragePort
from forward.core.paper_ledger_ports import PaperDeploymentLedgerPort
from forward.core.portfolio_ports import (
    PortfolioStoragePort,
)


class DeploymentError(Exception):
    """A deployment action was refused. Carries the code the API returns."""

    def __init__(self, message: str, *, code: str = "deployment_error", status: int = 400):
        """Create an error with the response code and status for the API.

        Args:
            message: Short reason the deployment action was refused.
            code: Stable machine-readable error code.
            status: HTTP status used by the route.
        """
        self.code = code
        self.status = status
        super().__init__(message)


def _deployment_config(row: dict[str, Any]) -> dict[str, Any] | None:
    """Read a deployment config and refuse malformed saved values.

    Args:
        row: Deployment row that may contain a JSON string or a mapping.

    Returns:
        The saved config mapping, or None when the deployment has no config.

    Raises:
        DeploymentError: The saved config is not a valid mapping.
    """
    raw = row.get("config")
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise DeploymentError(
                "the saved deployment config is not valid JSON",
                code="invalid_deployment_config",
                status=422,
            ) from exc
    if not isinstance(raw, dict):
        raise DeploymentError(
            "the saved deployment config must be an object",
            code="invalid_deployment_config",
            status=422,
        )
    return raw


@dataclass
class DeploymentService:
    """The paper deployment lifecycle: create, start, pause, stop, reset.

    A deployment is what gives a paper account a *capital allocation* and a
    *strategy version*, which is what makes per-strategy P&L and per-strategy risk
    limits meaningful. Without one, the paper ledger has no starting cash and
    reports zero rather than a number somebody chose.

    Every state change that stops or restarts trading requires a reason, and the
    reason is stored on the row — the same rule the kill switch follows, for the
    same reason.
    """

    storage: PaperDeploymentStoragePort
    ledger: PaperDeploymentLedgerPort
    runner_interval_seconds: float
    runner_lookback_days: int
    forward_trade_storage: ForwardTradeStoragePort = field(kw_only=True)
    portfolio_storage: PortfolioStoragePort = field(kw_only=True)

    def create(
        self,
        user_id: str,
        *,
        strategy_id: str,
        strategy_version: int,
        capital: float,
        mode: str = "PAPER",
        broker_account: str | None = None,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        """Allocate capital to an immutable strategy version.

        **The pinned version is checked before the row is written**, and only when
        the strategy is one of the caller's saved strategies. Two refusals:

        * the version does not exist;
        * the version exists but resolves to no live entry/exit rules.

        Both would otherwise produce a deployment that reports itself ``RUNNING``,
        shows up on the monitor, and never places an order — the runner refuses to
        trade a version it cannot resolve, correctly, because the alternative is
        stamping a strategy's name onto rules it never ran. That refusal is honest
        and it is also indistinguishable from a quiet market on the screen, so it
        is better raised here where the operator is still looking.

        A ``strategy_id`` that is **not** one of the caller's saved strategies is
        left alone. That path is deliberate and tested (``tests/
        test_paper_deployment.py``): a deployment may name a bare registry key, and
        the runner reports the refusal on the status surface when it cannot be
        resolved. Widening the check to reject unknown ids would change behaviour
        those tests pin, and it would not catch a mistake it does not already
        catch — a typo in a *saved* strategy's id is caught by the version check.
        """
        from forward.paper.config import RunnerConfig
        from forward.strategy.definition import parse_definition, resolve_rules

        if not isinstance(config, dict):
            raise DeploymentError(
                "deployment config must be an object",
                code="invalid_deployment_config",
                status=422,
            )
        definition: dict[str, Any] | None = None

        with self.storage.unit_of_work() as deployment_storage:
            if deployment_storage.has_saved_strategy(user_id, strategy_id):
                row = deployment_storage.strategy_version(strategy_id, int(strategy_version))
                if row is None:
                    raise DeploymentError(
                        f"strategy {strategy_id} has no version {strategy_version}, so "
                        "there is nothing to deploy",
                        code="version_not_found",
                        status=422,
                    )
                definition, _reason = parse_definition(row.get("definition"))
                from forward.strategy.strategies import paper_deployment_settings

                try:
                    strategy_settings = paper_deployment_settings(definition or {})
                except (ValueError, TypeError, KeyError) as exc:
                    raise DeploymentError(
                        str(exc), code="invalid_strategy_deployment", status=422
                    ) from exc
                if strategy_settings and str(mode).upper() != "PAPER":
                    raise DeploymentError(
                        "This strategy supports paper deployment only",
                        code="paper_only",
                        status=422,
                    )
                config = {**config, **strategy_settings}
                resolution = resolve_rules(definition) if definition is not None else None
                if not (resolution and resolution.ok):
                    why = resolution.reason if resolution else "the definition is unreadable"
                    raise DeploymentError(
                        f"strategy version {strategy_id}#{strategy_version} cannot be "
                        f"deployed: {why}",
                        code="version_not_deployable",
                        status=422,
                    )

                try:
                    RunnerConfig(
                        symbols=(),
                        interval_seconds=self.runner_interval_seconds,
                        lookback_days=self.runner_lookback_days,
                    ).apply_context(definition)
                except ValueError as exc:
                    raise DeploymentError(
                        str(exc), code="unsupported_strategy_context", status=422
                    ) from exc

            try:
                RunnerConfig.from_deployment(
                    {"config": config, "definition": definition},
                    platform_interval_seconds=self.runner_interval_seconds,
                    platform_lookback_days=self.runner_lookback_days,
                )
            except ValueError as exc:
                raise DeploymentError(
                    str(exc), code="invalid_deployment_config", status=422
                ) from exc

            from forward.services.portfolio_risk import check_strategy_capital

            if str(mode or "").upper() == "LIVE":
                # The money decision, and the only creation this gate touches.
                # Paper runs always pass: evidence must come from somewhere,
                # and refusing first runs would make learning impossible.
                from forward.services.gating import check_live_deployment

                check_live_deployment(
                    strategy_id,
                    int(strategy_version),
                    user_id=user_id,
                    storage=self.forward_trade_storage,
                )

            ok, reason = check_strategy_capital(
                user_id,
                strategy_id,
                float(capital),
                storage=self.portfolio_storage,
            )
            if not ok:
                raise DeploymentError(
                    reason or "capital allocation exceeds strategy cap",
                    code="strategy_capital_cap_exceeded",
                    status=422,
                )

            return deployment_storage.create_deployment(
                user_id=user_id,
                strategy_id=strategy_id,
                strategy_version=int(strategy_version),
                mode=mode,
                capital=capital,
                broker_account=broker_account,
                config=config,
            )

    def launch_challenger(
        self,
        user_id: str,
        *,
        strategy_id: str,
        champion_deployment_id: str,
        challenger_version: int,
    ) -> dict[str, Any]:
        """Launch a challenger version against a running champion, same conditions.

        The challenger inherits the champion's capital, universe and full
        deployment config — the only thing that differs is the strategy
        version, which is what makes the comparison a comparison rather than
        two unrelated books. Market data, venue, trading costs and the
        slippage model are shared structurally (one runner, one venue), so
        they cannot differ; capital and universe are copied here, so they
        cannot drift through a hand-typed form either.

        Refusals, each with its own code rather than a bare 422:

        * the champion deployment is not the caller's, not PAPER, or not
          RUNNING — a challenger needs live conditions to be compared against;
        * the challenger version equals the champion's — that is the same arm
          twice, and two loops on one version would even share idempotency
          keys, so the second loop's orders would collapse into the first's;
        * the challenger version does not exist or resolves to no rules
          (via :meth:`create`, which owns those checks);
        * a RUNNING PAPER deployment already exists for the challenger version
          — for the same idempotency reason: one version, one loop.

        Returns the challenger deployment row plus a ``parity`` statement
        naming what was copied from which champion, so the identical-conditions
        claim is auditable rather than asserted.
        """
        champion = self.get(user_id, champion_deployment_id)
        if str(champion.get("strategy_id") or "") != str(strategy_id):
            raise DeploymentError(
                f"the champion deployment does not belong to strategy {strategy_id}",
                code="champion_mismatch",
                status=422,
            )
        if str(champion.get("mode") or "").upper() != "PAPER":
            raise DeploymentError(
                "a challenger can only be launched against a PAPER champion",
                code="champion_not_paper",
                status=422,
            )
        if str(champion.get("status") or "").upper() != "RUNNING":
            raise DeploymentError(
                "a challenger can only be launched against a RUNNING champion; "
                "start it first so both arms observe live conditions",
                code="champion_not_running",
                status=409,
            )
        if int(challenger_version) == int(champion.get("strategy_version") or 0):
            raise DeploymentError(
                f"version {challenger_version} is already the champion; a "
                "challenger must be a different version",
                code="challenger_is_champion",
                status=422,
            )
        clashes = self.storage.running_deployments(user_id)
        for row in clashes:
            if str(row.get("strategy_id") or "") == str(strategy_id) and int(
                row.get("strategy_version") or 0
            ) == int(challenger_version):
                raise DeploymentError(
                    f"version {challenger_version} already has a RUNNING PAPER "
                    f"deployment ({row.get('deployment_id')}); one version runs "
                    "one loop, because two loops on one version share "
                    "idempotency keys",
                    code="challenger_already_running",
                    status=409,
                )

        import copy

        saved_config = _deployment_config(champion)
        config = copy.deepcopy(saved_config)

        created = self.create(
            user_id,
            strategy_id=strategy_id,
            strategy_version=int(challenger_version),
            capital=float(champion.get("capital") or 0.0),
            mode="PAPER",
            broker_account=champion.get("broker_account"),
            config=config,
        )
        created["parity"] = {
            "champion_deployment_id": champion.get("deployment_id"),
            "capital_copied": float(champion.get("capital") or 0.0),
            "config_copied": config,
            "venue_shared": True,
            "note": (
                "market data, trading costs and the slippage model are shared "
                "structurally: both loops fill against the runner's single "
                "venue. Capital and universe were copied from the champion row."
            ),
        }
        return created

    def list(self, user_id: str, *, status: str | None = None) -> list[dict[str, Any]]:
        """List owned deployments and attach their current account snapshots.

        Args:
            user_id: Account that owns the deployments.
            status: Optional lifecycle status filter.

        Returns:
            Deployment records with current P&L snapshots.
        """
        rows = self.storage.deployments(user_id, status)
        snapshots = self.ledger.snapshots_for_deployments(user_id, rows)
        for row in rows:
            row["pnl"] = snapshots[row["deployment_id"]]
        return rows

    def summary(self, user_id: str) -> dict[str, int]:
        """Return deployment counts for the paper navigation health display.

        Args:
            user_id: The owner of the deployments.

        Returns:
            The total count and the number of running deployments.
        """
        rows = self.storage.deployments(user_id, None)
        return {
            "total": len(rows),
            "running_count": sum(row["status"] == "RUNNING" for row in rows),
        }

    def get(self, user_id: str, deployment_id: str) -> dict[str, Any]:
        """Read one deployment owned by the account.

        Args:
            user_id: Account that must own the deployment.
            deployment_id: Deployment identifier.

        Returns:
            The saved deployment record.
        """
        row = self.storage.deployment(user_id, deployment_id)
        if row is None:
            raise DeploymentError("no such deployment", code="not_found", status=404)
        return row

    def start(self, user_id: str, deployment_id: str) -> dict[str, Any]:
        """Start one pending or paused deployment.

        Args:
            user_id: Account that owns the deployment.
            deployment_id: Deployment to start.

        Returns:
            The updated deployment record.
        """
        self.get(user_id, deployment_id)
        if self.storage.start(user_id, deployment_id) == 0:
            raise DeploymentError("no such deployment", code="not_found", status=404)
        return self.get(user_id, deployment_id)

    def pause(self, user_id: str, deployment_id: str, *, reason: str) -> dict[str, Any]:
        """Pause a running deployment and save the reason.

        Args:
            user_id: Account that owns the deployment.
            deployment_id: Deployment to pause.
            reason: Required operator reason.

        Returns:
            The updated deployment record.
        """
        self.get(user_id, deployment_id)
        try:
            changed = self.storage.pause(user_id, deployment_id, reason=reason)
        except ValueError as exc:
            # The repository refuses a blank reason. Translated here so the API can
            # answer 400 with a code rather than 500 — the same treatment `stop`
            # gives it, and the reason both go through the same path.
            raise DeploymentError(str(exc), code="reason_required") from exc
        if changed == 0:
            raise DeploymentError(
                "the deployment is not running, so it cannot be paused",
                code="not_running",
                status=409,
            )
        return self.get(user_id, deployment_id)

    def stop(self, user_id: str, deployment_id: str, *, reason: str) -> dict[str, Any]:
        """Stop a deployment and save the reason.

        Args:
            user_id: Account that owns the deployment.
            deployment_id: Deployment to stop.
            reason: Required operator reason.

        Returns:
            The updated deployment record.
        """
        self.get(user_id, deployment_id)
        try:
            changed = self.storage.stop(user_id, deployment_id, reason=reason)
        except ValueError as exc:
            raise DeploymentError(str(exc), code="reason_required") from exc
        if changed == 0:
            raise DeploymentError(
                "the deployment is already stopped", code="already_stopped", status=409
            )
        return self.get(user_id, deployment_id)

    def reset(self, user_id: str, deployment_id: str, *, reason: str) -> dict[str, Any]:
        """Stop this deployment and start a fresh one with the same configuration.

        **Reset does not delete anything, and it cannot.** ``order_events`` is
        append-only — that is what makes the order log trustworthy — so a paper
        account's history cannot be erased. Deleting the fills would leave orders
        whose fills had vanished, and reconciliation would report mismatches the
        platform had created itself.

        So a reset is a *new deployment*: the old one is stopped with the reason
        recorded, its history stays readable, and the returned deployment has a new
        id and the original capital. That is also what an operator actually wants —
        "start this strategy again from a clean account" — and it keeps the previous
        run's P&L available for comparison.
        """
        previous = self.get(user_id, deployment_id)
        reason = (reason or "").strip()
        if not reason:
            raise DeploymentError(
                "resetting a deployment requires a reason", code="reason_required"
            )
        config = _deployment_config(previous)
        self.storage.stop(user_id, deployment_id, reason=f"reset: {reason}")
        fresh = self.create(
            user_id,
            strategy_id=previous["strategy_id"],
            strategy_version=previous["strategy_version"],
            capital=previous["capital"],
            mode=previous["mode"],
            broker_account=previous.get("broker_account"),
            config=config,
        )
        fresh["reset_from"] = deployment_id
        fresh["reset_reason"] = reason
        return fresh

    # ------------------------------------------------------------------ reads
    def positions(
        self, user_id: str, deployment_id: str, *, prices: dict[str, float] | None = None
    ) -> dict[str, Any]:
        """Read the positions for one owned deployment.

        Args:
            user_id: Account that owns the deployment.
            deployment_id: Deployment whose positions are requested.
            prices: Optional current marks for open positions.

        Returns:
            Position rows, cash, equity, exposure, and price completeness.
        """
        self.get(user_id, deployment_id)
        snapshot = self.ledger.snapshot(user_id, deployment_id=deployment_id, prices=prices)
        return {
            "deployment_id": deployment_id,
            "positions": snapshot["positions"],
            "complete": snapshot["complete"],
            "unpriced_symbols": snapshot["unpriced_symbols"],
            "cash": snapshot["cash"],
            "equity": snapshot["equity"],
            "gross_exposure": snapshot["gross_exposure"],
        }

    def pnl(
        self, user_id: str, deployment_id: str, *, prices: dict[str, float] | None = None
    ) -> dict[str, Any]:
        """Read the P&L snapshot for one owned deployment.

        Args:
            user_id: Account that owns the deployment.
            deployment_id: Deployment whose P&L is requested.
            prices: Optional current marks for open positions.

        Returns:
            The deployment's current account snapshot.
        """
        self.get(user_id, deployment_id)
        return self.ledger.snapshot(user_id, deployment_id=deployment_id, prices=prices)

    def orders(
        self, user_id: str, deployment_id: str, *, limit: int
    ) -> list[dict[str, Any]]:
        """Read recent orders for one owned deployment.

        Args:
            user_id: Account that owns the deployment.
            deployment_id: Deployment whose orders are requested.
            limit: Maximum number of recent orders to return.

        Returns:
            Recent order records in storage order.
        """
        self.get(user_id, deployment_id)
        return self.storage.orders(user_id, deployment_id, limit=limit)
