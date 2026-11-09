"""Circular moving-block bootstrap of recorded portfolio returns.

Resamples contiguous blocks to retain short-range dependence. This is conditional
on observed history, not a forecast or a substitute for out-of-sample testing.
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np

from forward.compute.device import get_device


def _quantiles(values: list[float]) -> dict[str, float]:
    """Return the requested bootstrap percentiles.

    Args:
        values: Results from all simulated paths.

    Returns:
        The fifth, fiftieth, and ninety-fifth percentiles.
    """
    probabilities = (0.05, 0.5, 0.95)
    labels = ("p05", "p50", "p95")
    return {
        label: float(value)
        for label, value in zip(labels, np.quantile(values, probabilities), strict=True)
    }


def bootstrap_equity(
    equity: Any,
    *,
    paths: int = 5000,
    block: int = 5,
    seed: int = 42,
    device: str = "auto",
) -> dict[str, Any]:
    """Estimate conditional return and drawdown ranges from an equity curve.

    Args:
        equity: One-dimensional sequence of positive equity observations.
        paths: Number of resampled return paths.
        block: Number of adjacent returns in each sampled block.
        seed: Random seed used to make the result repeatable.
        device: Compute device name accepted by ``get_device``.

    Returns:
        Conditional return, drawdown, loss-rate, and run details.

    Raises:
        ValueError: If the curve or simulation settings are invalid.
        RuntimeError: If the requested compute device is unavailable.
    """
    import torch

    values = np.asarray(equity, dtype=np.float64)
    if (
        values.ndim != 1
        or len(values) < 31
        or not np.isfinite(values).all()
        or (values <= 0).any()
    ):
        raise ValueError("At least 31 finite, positive equity observations are required.")
    if not 100 <= paths <= 10000 or not 1 <= block <= 60:
        raise ValueError("Use 100–10000 paths and a block length of 1–60 observations.")
    returns = values[1:] / values[:-1] - 1
    n = len(returns)
    if block > n:
        raise ValueError("Block length exceeds the available return history.")
    dev = get_device(device)
    started = time.perf_counter()
    rng = np.random.default_rng(seed)
    source = torch.as_tensor(returns, dtype=torch.float64, device=dev)
    offsets = np.arange(block)
    totals, drawdowns = [], []
    # Bound GPU memory even for long intraday histories.
    batch_size = max(1, min(256, 500000 // n))
    for start in range(0, paths, batch_size):
        count = min(batch_size, paths - start)
        starts = rng.integers(0, n, size=(count, (n + block - 1) // block))
        indices = ((starts[..., None] + offsets) % n).reshape(count, -1)[:, :n]
        sampled = source[torch.as_tensor(indices, device=dev)]
        curve = torch.cumprod(1 + sampled, dim=1)
        peaks = torch.cummax(curve, dim=1).values.clamp(min=1)
        totals.extend((curve[:, -1] - 1).cpu().tolist())
        drawdowns.extend((curve / peaks - 1).min(dim=1).values.cpu().tolist())
    return {
        "method": "circular_moving_block_bootstrap",
        "device": dev.type,
        "paths": paths,
        "block_length": block,
        "seed": seed,
        "observations": n,
        "seconds": time.perf_counter() - started,
        "total_return": _quantiles(totals),
        "max_drawdown": _quantiles(drawdowns),
        "loss_fraction": float(np.mean(np.asarray(totals) < 0)),
        "assumption": "Resamples blocks of observed net portfolio returns over the same horizon. No new market regimes or trade execution are simulated.",
    }
