import numpy as np
import pytest

pytest.importorskip("torch")
from forward.compute.bootstrap import bootstrap_equity


def test_constant_positive_returns_have_no_drawdown():
    result = bootstrap_equity(100 * 1.01 ** np.arange(40), paths=100, device="cpu")
    assert result["max_drawdown"]["p05"] == 0
    assert result["loss_fraction"] == 0
    assert result["total_return"]["p50"] == pytest.approx(1.01 ** 39 - 1)


def test_first_loss_counts_from_starting_capital():
    result = bootstrap_equity(100 * .99 ** np.arange(40), paths=100, device="cpu")
    assert result["max_drawdown"]["p50"] == pytest.approx(.99 ** 39 - 1)
    assert result["loss_fraction"] == 1


def test_cuda_and_cpu_use_identical_resamples():
    import torch
    if not torch.cuda.is_available():
        pytest.skip("CUDA unavailable")
    equity = 100 * np.cumprod(1 + np.random.default_rng(9).normal(0, .01, 100))
    cpu = bootstrap_equity(equity, paths=100, device="cpu")
    gpu = bootstrap_equity(equity, paths=100, device="cuda")
    assert cpu["total_return"] == pytest.approx(gpu["total_return"])
    assert cpu["max_drawdown"] == pytest.approx(gpu["max_drawdown"])


def test_invalid_equity_is_rejected():
    with pytest.raises(ValueError):
        bootstrap_equity([100, 0, 90], paths=100)
