"""CLI commands for compute-device details and parameter sweeps."""

from __future__ import annotations

from argparse import Namespace


def _inclusive_range(text: str) -> list[int]:
    """Parse an inclusive integer range written as start:stop:step.

    Args:
        text: Colon-separated start, stop, and step values.

    Returns:
        The inclusive sequence of integers.
    """
    start, stop, step = (int(value) for value in text.split(":"))
    return list(range(start, stop + 1, step))


def run_gpu(args: Namespace) -> int:
    """Show device details or run an SMA parameter sweep.

    Args:
        args: Parsed command-line options for the GPU command.

    Returns:
        The process exit code.
    """
    from forward.compute import device_info

    if args.action == "info":
        for key, value in device_info().items():
            print(f"{key:>18}: {value}")
        return 0

    from forward.compute.sweep import close_matrix, sma_cross_sweep

    close, names, _dates = close_matrix(days=args.days)
    fasts, slows = _inclusive_range(args.fast), _inclusive_range(args.slow)
    print(
        f"{len(names)} symbols x {close.shape[0]} bars; "
        f"fast={fasts[0]}..{fasts[-1]} slow={slows[0]}..{slows[-1]}"
    )

    def run(device: str):
        return sma_cross_sweep(
            close,
            fasts,
            slows,
            device=device,
            cost_bps=args.cost_bps,
            chunk=args.chunk,
        )

    result = run(args.device)
    print(f"{result.combos} pairs on {result.device} in {result.seconds:.2f}s")
    if args.compare and result.device != "cpu":
        run(result.device)
        gpu = run(result.device)
        cpu = run("cpu")
        print(f"gpu {gpu.seconds:.2f}s vs cpu {cpu.seconds:.2f}s -> {cpu.seconds / gpu.seconds:.1f}x")
    print(f"{'fast':>5} {'slow':>5} {'sharpe':>7} {'return':>8} {'maxDD':>7} {'entries':>8}")
    for row in result.top(args.top):
        print(
            f"{row['fast']:>5} {row['slow']:>5} {row['sharpe']:>7.2f} "
            f"{row['total_return']:>8.1%} {row['max_drawdown']:>7.1%} {row['entries']:>8}"
        )
    print(
        "Screening pass: next-bar close-to-close, equal weight, flat costs. "
        "Confirm candidates with `forward backtest`."
    )
    return 0
