"""Format learning and drift reports for the command line."""

from __future__ import annotations

from typing import Any


def format_performance(analysis: Any, limit: int) -> str:
    """Format measured strategy performance by market condition.

    Args:
        analysis: Performance analysis to format.
        limit: Maximum number of rows to include.

    Returns:
        The formatted report text.
    """
    lines: list[str] = []
    lines.append(f"strategy   {analysis.strategy}")
    lines.append(f"metric     {analysis.metric}")
    lines.append(f"trades     {analysis.n}")

    overall = analysis.overall or {}
    if overall.get("n"):
        mean = overall.get("mean")
        low, high = overall.get("ci_low"), overall.get("ci_high")
        interval = f" [95% CI {(low or 0):,.1f} … {(high or 0):,.1f}]" if low is not None else ""
        lines.append(f"expectancy {'—' if mean is None else f'{mean:,.1f}'} per trade{interval}")
        if overall.get("win_rate") is not None:
            lines.append(f"win rate   {overall['win_rate']:.1%} of {overall['n']}")
        if overall.get("profit_factor") is not None:
            lines.append(f"PF         {overall['profit_factor']:.2f}")
        else:
            lines.append("PF         undefined — no losing trades in the sample")
    for caveat in analysis.caveats:
        lines.append(f"caveat     {caveat}")

    for breakdown in analysis.breakdowns:
        coverage = (
            f"{breakdown.rows_with_value}/{breakdown.rows_scanned}"
            if breakdown.rows_scanned
            else "0/0"
        )
        lines.append("")
        lines.append(f"--- by {breakdown.axis} ({breakdown.label}) — coverage {coverage} ---")
        if not breakdown.buckets:
            lines.append("    no row in the sample carries a value for this axis")
            continue
        lines.append(f"{'bucket':<26}{'n':>6}{'mean':>12}{'lift':>12}  {'significance':<16}")
        # ``lift`` is the difference between this bucket's mean and the baseline
        # mean — in the metric's own units, not a ratio. Rendering it as a
        # percentage would report a 2.8-rupee gap as "280%".
        percentage = analysis.metric == "return_pct"
        for bucket in breakdown.buckets[:limit]:
            stats = bucket.get("stats") or {}
            mean = stats.get("mean")
            lift = bucket.get("lift")
            lift_text = "—"
            if lift is not None:
                lift_text = f"{lift:+.2f}%" if percentage else f"{lift:+,.2f}"
            lines.append(
                f"{str(bucket.get('label')):<26}{bucket.get('n', 0):>6}"
                f"{'—' if mean is None else f'{mean:,.2f}':>12}"
                f"{lift_text:>12}  "
                f"{bucket.get('significance', ''):<16}"
            )
            if bucket.get("suppressed"):
                lines.append(f"{'':<26}suppressed — {bucket.get('note')}")
        if len(breakdown.buckets) > limit:
            lines.append(f"    … {len(breakdown.buckets) - limit} more bucket(s); raise --rows")

    if analysis.notable:
        lines.append("")
        lines.append("--- notable (survived both the sample floor and the correction) ---")
        for bucket in analysis.notable[:limit]:
            stats = bucket.get("stats") or {}
            lines.append(
                f"  [{bucket.get('significance')}] {bucket.get('axis')}="
                f"{bucket.get('label')} n={bucket.get('n')} "
                f"mean={stats.get('mean')} lift={bucket.get('lift')} "
                f"p={bucket.get('p_value')} (adjusted {bucket.get('p_adjusted')})"
            )
    else:
        lines.append("")
        lines.append("--- notable ---")
        lines.append("  none: no bucket separated from its baseline after correction")
    return "\n".join(lines)


def format_drift(analysis: Any, limit: int) -> str:
    """Format comparisons between strategy result sources.

    Args:
        analysis: Drift analysis to format.
        limit: Maximum number of metrics to include.

    Returns:
        The formatted report text.
    """
    lines: list[str] = []
    present = ", ".join(f"{k}={v}" for k, v in analysis.available_sources.items())
    lines.append(f"strategy   {analysis.strategy}")
    lines.append(f"sources    {present}")

    if not analysis.pairs:
        lines.append("")
        lines.append("no two sources hold trades, so no comparison could be formed")
    for pair in analysis.pairs:
        lines.append("")
        lines.append(
            f"--- {pair.reference} ({pair.reference_n}) vs "
            f"{pair.comparison} ({pair.comparison_n}) — {pair.status} ---"
        )
        if pair.status != "ok":
            for item in pair.limitations:
                lines.append(f"  {item}")
        lines.append(
            f"{'metric':<22}{'baseline':>11}{'live':>11}{'delta':>11}"
            f"{'relative':>10}  {'verdict':<26}"
        )
        for item in pair.metrics[:limit]:
            if item.status != "ok":
                lines.append(f"{item.label:<22}{'—':>11}{'—':>11}{'—':>11}{'—':>10}  {item.status}")
                if item.reason:
                    lines.append(f"{'':<22}{item.reason}")
                continue
            lines.append(
                f"{item.label:<22}"
                f"{item.reference_value:>11,.2f}{item.comparison_value:>11,.2f}"
                f"{item.delta:>+11,.2f}"
                f"{'—' if item.relative is None else f'{item.relative * 100:+.1f}%':>10}  "
                f"{item.direction + ' / ' + item.magnitude:<26}"
            )
        if len(pair.metrics) > limit:
            lines.append(f"    … {len(pair.metrics) - limit} more metric(s); raise --rows")

        if pair.regime.get("status") == "ok":
            lines.append("")
            lines.append(f"  regime mix: {pair.regime['summary']}")

    if analysis.findings:
        lines.append("")
        lines.append("--- findings (advisory — nothing is applied) ---")
        for item in analysis.findings:
            lines.append(f"  [{item.get('severity')}] {item.get('statement')}")
            if item.get("evidence"):
                lines.append(f"      evidence  {item['evidence']}")

    if analysis.limitations:
        lines.append("")
        lines.append("--- limitations ---")
        for item in analysis.limitations:
            lines.append(f"  - {item}")
    return "\n".join(lines)


def format_report(report: Any, limit: int) -> str:
    """Format the daily learning report for terminal output.

    Args:
        report: Daily report to format.
        limit: Maximum number of rows to include.

    Returns:
        The formatted report text.
    """
    lines: list[str] = []
    lines.append(f"as of        {report.as_of} (window {report.window_days}d)")
    lines.append(f"metric       {report.metric} ({report.aggregate})")
    if report.metric_note:
        lines.append(f"             {report.metric_note}")
    lines.append(f"today        {report.trades_today} trade(s)")
    if report.metric_total is not None:
        # The unit travels with the number: ``net_pnl`` is money, ``return_pct``
        # is a percentage, and a bare figure in front of a reader who cannot tell
        # which is the misread this line exists to prevent.
        unit = "%" if report.metric == "return_pct" else ""
        lines.append(f"{report.metric:<12} {report.metric_total:,.2f}{unit}")
    if report.net_pnl_today is not None:
        lines.append(f"net pnl      {report.net_pnl_today:,.2f}")

    expectation = report.expectation or {}
    if expectation.get("mean_per_day") is not None:
        lines.append(
            f"expected     {expectation['mean_per_day']:,.2f}/day "
            f"over {expectation.get('trading_days', '?')} trading day(s) with a trade"
        )
    deviation = report.deviation or {}
    if deviation.get("relative") is not None:
        lines.append(f"deviation    {deviation['relative'] * 100:+.1f}% vs that baseline")

    regime = report.regime or {}
    if regime.get("tied"):
        lines.append(f"regime       tied — {', '.join(regime['tied'])}")
    elif regime.get("label"):
        counts = ", ".join(f"{k}={v}" for k, v in (regime.get("counts") or {}).items())
        lines.append(f"regime       {regime['label']} ({counts})")

    for block, title in (
        (report.by_strategy, "by strategy"),
        (report.strongest, "strongest setups"),
        (report.weakest, "weakest setups"),
    ):
        if not block:
            continue
        lines.append("")
        lines.append(f"--- {title} ---")
        for row in block[:limit]:
            label = (
                row.get("strategy")
                or row.get("setup")
                or row.get("label")
                or row.get("regime")
                or row.get("symbol")
                or "?"
            )
            sample = row.get("n", row.get("trades", 0))
            value = row.get("net_pnl", row.get("mean", row.get("value")))
            shown = "—" if value is None else f"{value:,.2f}"
            lines.append(f"  {str(label):<28}{sample:>5}  {shown}")

    if report.unusual:
        lines.append("")
        lines.append("--- unusual ---")
        for item in report.unusual:
            lines.append(f"  {item}")

    if report.execution:
        lines.append("")
        lines.append("--- execution ---")
        for key, value in report.execution.items():
            lines.append(f"  {key:<24}{value}")

    if report.observations:
        lines.append("")
        lines.append("--- observations (advisory — nothing is applied) ---")
        for obs in report.observations:
            lines.append(f"  [{obs.get('severity')}] {obs.get('statement')}")
            lines.append(f"      evidence    {obs.get('evidence')}")
            lines.append(f"      confidence  {obs.get('confidence')} (n={obs.get('sample_size')})")

    if report.drift:
        lines.append("")
        lines.append("--- drift ---")
        for key, value in report.drift.items():
            lines.append(f"  {key:<24}{value}")

    if report.limitations:
        lines.append("")
        lines.append("--- limitations ---")
        for item in report.limitations:
            lines.append(f"  - {item}")
    return "\n".join(lines)
