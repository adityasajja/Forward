"""Pick the compute device, and say plainly what it is."""

from __future__ import annotations

from typing import Any


def _torch():
    import torch  # imported lazily: it is large and the app does not need it to serve pages

    return torch


def get_device(prefer: str = "auto"):
    """Select a requested compute device.

    Args:
        prefer: ``"auto"`` selects CUDA when available and CPU otherwise.
            ``"cpu"`` and ``"cuda"`` require that exact device choice.

    Returns:
        The selected PyTorch device.

    Raises:
        ValueError: If ``prefer`` is not ``"auto"``, ``"cpu"``, or ``"cuda"``.
        RuntimeError: If CUDA is requested but unavailable.
    """
    torch = _torch()
    if prefer not in {"auto", "cpu", "cuda"}:
        raise ValueError("Device must be 'auto', 'cpu', or 'cuda'.")
    if prefer == "cpu":
        return torch.device("cpu")
    if prefer == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError(
                "CUDA was requested but this PyTorch cannot see a GPU "
                f"(torch {torch.__version__}, built for CUDA {torch.version.cuda})"
            )
        return torch.device("cuda")
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def device_info() -> dict[str, Any]:
    """What this machine offers, for `forward gpu info` and the status page."""
    torch = _torch()
    info: dict[str, Any] = {
        "torch": torch.__version__,
        "built_for_cuda": torch.version.cuda,
        "cuda_available": bool(torch.cuda.is_available()),
        "device": "cpu",
    }
    if info["cuda_available"]:
        props = torch.cuda.get_device_properties(0)
        free, total = torch.cuda.mem_get_info(0)
        info.update(
            device="cuda",
            name=props.name,
            memory_total_mb=round(total / 2**20),
            memory_free_mb=round(free / 2**20),
            compute_capability=f"{props.major}.{props.minor}",
        )
    return info
