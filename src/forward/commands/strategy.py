"""CLI commands for strategy authoring and inspection."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path


def resolve_cli_user(explicit: str | None) -> str:
    """Resolve the account for a CLI strategy write.

    Args:
        explicit: Optional account ID selected by the caller.

    Returns:
        The selected account ID.

    Raises:
        SystemExit: If no account exists, the ID is unknown, or the choice is ambiguous.
    """

    from forward.wiring.auth import get_auth_service

    auth = get_auth_service()
    if explicit:
        user = auth.get_user(explicit)
        if user is None:
            raise SystemExit(f"no account with id {explicit!r}")
        return explicit
    rows = auth.list_users()
    if not rows:
        raise SystemExit("no accounts exist yet — start the server and bootstrap one")
    if len(rows) > 1:
        names = ", ".join(f"{r['username']}={r['user_id']}" for r in rows)
        raise SystemExit(f"more than one account ({names}); pass --user")
    return rows[0]["user_id"]


def run_strategy(args: Namespace) -> int:
    """Create, inspect, validate, or list strategies from the command line.

    Args:
        args: Parsed command-line options for the strategy command.

    Returns:
        The process exit code.
    """

    from forward.services.strategies import StrategyError
    from forward.wiring.strategy import get_strategy_service

    service = get_strategy_service()
    user_id = resolve_cli_user(args.user)
    # Both spellings, one value. `show <id>` and `show --strategy-id <id>` are
    # the same request; accepting only one of them is a trap for the caller.
    strategy_id = args.strategy_id or getattr(args, "target", None)

    def _emit(payload: dict) -> None:
        if args.json_path:
            Path(args.json_path).write_text(
                json.dumps(payload, indent=2, default=str), encoding="utf-8"
            )

    def _report(report: dict) -> None:
        status = "OK" if report["ok"] else "INVALID"
        print(f"validation: {status}")
        for err in report["errors"]:
            print(f"  error   {err['field']}: {err['message']}")
        for warn in report["warnings"]:
            print(f"  warning {warn['field']}: {warn['message']}")
        paper = report.get("paper") or {}
        print(
            f"  paper path:    {'deployable' if paper.get('resolvable') else 'NOT deployable'}"
            + (f" — {paper['reason']}" if paper.get("reason") else "")
        )
        backtest = report.get("backtest") or {}
        print(
            f"  backtest path: {'runnable' if backtest.get('resolvable') else 'NOT runnable'}"
            + (f" — {backtest['reason']}" if backtest.get("reason") else "")
        )
        # Said out loud on every validation, because a green tick next to a
        # strategy name is otherwise read as a finding.
        print(f"  {report['statistical_validation']['reason']}")

    def _load_definition() -> object:
        if not args.definition:
            raise SystemExit("--definition <file.json> is required")
        return json.loads(Path(args.definition).read_text(encoding="utf-8"))

    try:
        if args.action == "seed":
            from forward.strategy.example import EXAMPLE_NAME

            result = service.seed_example(user_id, name=args.name or EXAMPLE_NAME)
            strategy, version = result["strategy"], result["version"]
            print(
                f"{'created' if result['created'] else 'already present'}: "
                f"{strategy['name']} ({strategy['strategy_id']}) "
                f"version {version['version']}"
            )
            if result["note"]:
                print(f"  {result['note']}")
            print(f"  deployable: {version['deployable']}")
            if version.get("validation"):
                _report(version["validation"])
            print(
                "\nnext: POST /api/v1/paper/deployments with "
                f'{{"strategy_id": "{strategy["strategy_id"]}", '
                f'"strategy_version": {version["version"]}, "capital": <ENTER_CAPITAL_AMOUNT>}}'
            )
            print(
                "  Replace <ENTER_CAPITAL_AMOUNT> with the numeric capital you choose before sending."
            )
            _emit(result)
            return 0

        if args.action == "list":
            rows = service.list(user_id)
            if not rows:
                print("no strategies — `forward strategy seed` creates the worked example")
                return 0
            for row in rows:
                latest = row["latest_version"]
                state = "no versions" if latest is None else f"v{latest}"
                if latest is not None and not row["deployable"]:
                    state += " (not deployable)"
                print(f"{row['strategy_id']}  {state:<22} {row['name']}")
            _emit({"strategies": rows})
            return 0

        if args.action == "show":
            if not strategy_id:
                raise SystemExit("--strategy-id (or a positional id) is required")
            strategy = service.get(user_id, strategy_id)
            print(f"{strategy['name']}  ({strategy['strategy_id']}, {strategy['kind']})")
            if strategy.get("description"):
                print(f"  {strategy['description']}")
            for row in service.versions(user_id, strategy_id):
                note = f"  — {row['change_note']}" if row.get("change_note") else ""
                flag = "" if row["deployable"] else "  NOT deployable"
                print(f"  v{row['version']}  {row['created_at']}{flag}{note}")
                if not row["deployable"]:
                    print(f"      {row['not_deployable_reason']}")
            _emit(strategy)
            return 0

        if args.action == "create":
            if not args.name:
                raise SystemExit("--name is required")
            row = service.create(
                user_id,
                name=args.name,
                kind=args.kind,
                description=args.description,
                engine_key=args.engine_key,
            )
            print(f"created {row['name']} ({row['strategy_id']})")
            print(
                "it has no version yet, so it cannot be deployed — add one with "
                "`forward strategy version`"
            )
            _emit(row)
            return 0

        if args.action == "version":
            if not strategy_id:
                raise SystemExit("--strategy-id (or a positional id) is required")
            row = service.create_version(
                user_id,
                strategy_id,
                definition=_load_definition(),
                change_note=args.change_note,
            )
            print(f"created version {row['version']} of {strategy_id}")
            print(f"  deployable: {row['deployable']}")
            if row.get("not_deployable_reason"):
                print(f"  {row['not_deployable_reason']}")
            _report(row["validation"])
            _emit(row)
            return 0

        # validate
        if not strategy_id:
            raise SystemExit("--strategy-id (or a positional id) is required")
        report = service.validate(
            user_id,
            strategy_id,
            version=args.version,
            definition=_load_definition() if args.definition else None,
        )
        where = "draft" if not report.get("stored") else f"stored v{report['version']}"
        print(f"validating {where} definition of {strategy_id}")
        _report(report)
        _emit(report)
        return 0 if report["ok"] else 1

    except StrategyError as exc:
        print(f"refused ({exc.code}): {exc}")
        return 1
