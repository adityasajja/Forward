"""Read-only learning and performance commands."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

from loguru import logger

from forward.commands.learning_report import format_drift, format_performance, format_report


def run_learn(args: Namespace) -> int:
    """Inspect trade history without changing strategies or deployments.

    Args:
        args: Parsed command-line options for the learning command.

    Returns:
        The process exit code.
    """

    from forward.composition import get_learning_service
    from forward.research import learning_axes as axes_mod
    from forward.research import learning_stats as stats

    svc = get_learning_service()

    if args.action == "status":
        payload = svc.status()
        if args.json_path:
            Path(args.json_path).write_text(
                json.dumps(payload, indent=2, default=str), encoding="utf-8"
            )
        if not payload.get("available"):
            print(f"learning unavailable — {payload.get('reason')}")
            return 1
        counts = payload["counts"]
        print(
            f"backtest_runs      {counts['backtest_runs']} ({counts['completed_runs']} completed)"
        )
        print(f"backtest_trades    {counts['backtest_trades']}")
        journal_forward = counts.get("trade_journal_forward", 0)
        journal_note = f" ({journal_forward} forward)" if counts["trade_journal"] else ""
        print(f"trade_journal      {counts['trade_journal']}{journal_note}")
        if counts.get("paper_ledger_trades"):
            forward = counts.get("paper_ledger_forward_trades", 0)
            print(
                f"paper ledger       {counts['paper_ledger_trades']} settled "
                f"({forward} forward, {counts['paper_ledger_trades'] - forward} in-sample)"
            )
        # Two figures, because they answer two questions. "Is there enough to
        # analyse?" and "may anything be claimed?" are not the same number, and
        # a book of four hundred backfilled rows clears the first and fails the
        # second.
        forward_available = payload.get("forward_available", 0)
        print(f"usable trades      {payload['trades_available']}")
        print(f"forward evidence   {forward_available}")
        print(f"sample floor       {stats.MIN_SAMPLE} (MIN_SAMPLE)")
        print(
            "claimable          "
            + ("yes" if payload.get("sufficient_for_a_claim") else "no — below the floor")
        )
        # The note is always shown when it says something the counts do not: a
        # book that clears the sample floor while holding no forward trade is
        # exactly the state that reads as "we have plenty of data".
        note = payload.get("note") or ""
        if note and (not payload["sufficient_for_analysis"] or "forward" in note):
            print(f"note               {note}")
        if payload["missing_features"]:
            print("\nrequested but unavailable in this build:")
            for name, why in payload["missing_features"].items():
                print(f"  - {name}: {why}")
        return 0

    roles = tuple(r.strip() for r in args.roles.split(",") if r.strip()) if args.roles else None
    wanted = tuple(a.strip() for a in args.by.split(",") if a.strip()) if args.by else None
    if wanted:
        unknown = [a for a in wanted if a not in axes_mod.AXES_BY_NAME]
        if unknown:
            logger.error(
                "unknown axis/axes {!r}; available: {}",
                unknown,
                ", ".join(sorted(axes_mod.AXES_BY_NAME)),
            )
            return 2

    dataset = svc.dataset(refresh=True)

    if args.action == "dataset":
        summary = dataset.summary()
        if args.json_path:
            Path(args.json_path).write_text(
                json.dumps(summary, indent=2, default=str), encoding="utf-8"
            )
        print(
            f"rows               {summary['trades']} ({summary['closed_trades']} closed, "
            f"{summary['open_trades']} open)"
        )
        print(f"strategies         {', '.join(summary['strategies']) or '(none)'}")
        print(f"symbols            {summary['symbols']}")
        for source, count in summary["sources"].items():
            print(f"  {source:<16} {count}")
        grades = summary.get("evidence_grades") or {}
        if grades:
            print(
                f"evidence           {grades.get('forward', 0)} forward, "
                f"{grades.get('in_sample', 0)} in-sample"
            )
        # The date, not just the count. "We hold two forward trades" and "the
        # most recent one closed eleven months ago" are different findings about
        # whether anything is still being measured, and the count alone cannot
        # tell them apart.
        latest = summary.get("latest_forward_ts")
        if latest:
            print(f"latest forward     {str(latest)[:10]}")
        elif summary["trades"]:
            print("latest forward     none — no observation is graded forward")
        coverage = summary.get("metric_coverage") or {}
        if coverage:
            print(
                "outcome columns    "
                + ", ".join(f"{name} {count}" for name, count in coverage.items())
            )
        ledger = summary.get("paper_ledger") or {}
        if ledger.get("present"):
            print(f"paper ledger       {ledger.get('note')}")
        if summary["date_range"]["first"]:
            print(
                f"window             {summary['date_range']['first']} → {summary['date_range']['last']}"
            )
        if summary["net_pnl_total"] is not None:
            print(f"net pnl total      {summary['net_pnl_total']:,.2f}")
        if summary["missing_features"]:
            print("\nmissing features (recorded, not filled in):")
            for name, why in summary["missing_features"].items():
                print(f"  - {name}: {why}")
        for warning in summary["warnings"]:
            print(f"warning            {warning}")
        return 0

    if args.action == "performance":
        analysis = svc.performance(
            strategy=args.strategy,
            roles=roles,
            axes=list(wanted) if wanted else None,
            metric=args.metric,
        )
        payload = analysis.as_dict()
        if args.json_path:
            Path(args.json_path).write_text(
                json.dumps(payload, indent=2, default=str), encoding="utf-8"
            )
        print(format_performance(analysis, args.rows))
        return 0

    if args.action == "drift":
        analysis = svc.drift(
            strategy=args.strategy,
            reference=args.reference,
            roles=roles,
            min_sample=args.min_sample,
        )
        if args.json_path:
            Path(args.json_path).write_text(
                json.dumps(analysis.as_dict(), indent=2, default=str), encoding="utf-8"
            )
        print(analysis.headline)
        print()
        print(format_drift(analysis, args.rows))
        return 0

    if args.action == "report":
        report = svc.daily_report(window_days=args.window_days)
        if args.json_path:
            Path(args.json_path).write_text(
                json.dumps(report.as_dict(), indent=2, default=str), encoding="utf-8"
            )
        print(report.headline)
        print()
        print(format_report(report, args.rows))
        return 0

    payload = svc.snapshot(write=not args.no_write)
    if args.json_path:
        Path(args.json_path).write_text(
            json.dumps(payload, indent=2, default=str), encoding="utf-8"
        )
    print(payload["report"]["headline"])
    written = payload.get("written") or {}
    if written:
        for kind, path in written.items():
            print(f"wrote {kind:<8} {path}")
    else:
        print("(not written — pass without --no-write to persist)")
    return 0
