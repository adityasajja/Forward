"""Portfolio policy values and validation rules."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

CONFLICT_MODES = ("reject", "priority", "net")


class PolicyError(Exception):
    """A portfolio policy that cannot be stored."""

    def __init__(self, message: str, *, code: str = "invalid_policy", status: int = 422):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class PortfolioPolicy:
    """The validated portfolio policy. Every field ``None``-able: unset is off."""

    max_total_exposure: float | None = None
    max_daily_loss: float | None = None
    max_capital_per_strategy: float | None = None
    max_open_positions: int | None = None
    max_stock_exposure: float | None = None
    max_sector_exposure_pct: float | None = None
    max_correlated_exposure_pct: float | None = None
    correlation_groups: tuple[tuple[str, ...], ...] = ()
    conflict_mode: str = "reject"
    strategy_priorities: dict[str, float] = field(default_factory=dict)
    warn_at_pct_of_limit: float = 0.8

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_total_exposure": self.max_total_exposure,
            "max_daily_loss": self.max_daily_loss,
            "max_capital_per_strategy": self.max_capital_per_strategy,
            "max_open_positions": self.max_open_positions,
            "max_stock_exposure": self.max_stock_exposure,
            "max_sector_exposure_pct": self.max_sector_exposure_pct,
            "max_correlated_exposure_pct": self.max_correlated_exposure_pct,
            "correlation_groups": [list(g) for g in self.correlation_groups],
            "conflict_mode": self.conflict_mode,
            "strategy_priorities": dict(self.strategy_priorities),
            "warn_at_pct_of_limit": self.warn_at_pct_of_limit,
        }


def _positive_number(value: Any, name: str) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except TypeError, ValueError:
        raise PolicyError(f"{name} must be a number, got {value!r}") from None
    if not math.isfinite(number) or number <= 0:
        raise PolicyError(f"{name} must be positive, got {value!r}")
    return number


def _fraction(value: Any, name: str) -> float | None:
    number = _positive_number(value, name)
    if number is not None and number > 2.0:
        raise PolicyError(
            f"{name} is a fraction of capital (0.40 = 40%), got {value!r}; "
            "whole-number percentages are refused so 40 does not silently mean 4000%"
        )
    return number


def policy_from_dict(payload: dict[str, Any] | PortfolioPolicy | None) -> PortfolioPolicy:
    """Validate a policy payload. Unknown keys are refused, never ignored."""
    if isinstance(payload, PortfolioPolicy):
        return payload
    data = dict(payload or {})
    known = set(PortfolioPolicy.__dataclass_fields__)
    unknown = sorted(set(data) - known)
    if unknown:
        raise PolicyError(f"unknown policy field(s): {', '.join(unknown)}")

    mode = str(data.get("conflict_mode") or "reject").lower()
    if mode not in CONFLICT_MODES:
        raise PolicyError(
            f"conflict_mode must be one of {list(CONFLICT_MODES)}, got {data.get('conflict_mode')!r}"
        )

    groups: list[tuple[str, ...]] = []
    raw_groups = data.get("correlation_groups") or []
    if not isinstance(raw_groups, list):
        raise PolicyError("correlation_groups must be a list of symbol lists")
    for group in raw_groups:
        if not isinstance(group, list) or not group:
            raise PolicyError("correlation_groups must be a list of symbol lists")
        groups.append(tuple(str(s).strip().upper() for s in group if str(s).strip()))
    groups = [g for g in groups if g]

    priorities: dict[str, float] = {}
    raw_priorities = data.get("strategy_priorities") or {}
    if not isinstance(raw_priorities, dict):
        raise PolicyError("strategy_priorities must be {strategy_id: rank}")
    for key, rank in raw_priorities.items():
        try:
            priority = float(rank)
        except TypeError, ValueError:
            raise PolicyError(f"strategy_priorities[{key!r}] must be a number") from None
        if not math.isfinite(priority):
            raise PolicyError(f"strategy_priorities[{key!r}] must be finite")
        priorities[str(key)] = priority

    max_open = data.get("max_open_positions")
    if max_open is not None:
        try:
            max_open = int(max_open)
        except TypeError, ValueError:
            raise PolicyError("max_open_positions must be an integer") from None
        if max_open <= 0:
            raise PolicyError("max_open_positions must be positive")

    warn = data.get("warn_at_pct_of_limit", 0.8)
    try:
        warn = float(warn)
    except TypeError, ValueError:
        raise PolicyError("warn_at_pct_of_limit must be a number") from None
    if not math.isfinite(warn) or not (0.0 < warn <= 1.0):
        raise PolicyError("warn_at_pct_of_limit must be in (0, 1]")

    return PortfolioPolicy(
        max_total_exposure=_positive_number(data.get("max_total_exposure"), "max_total_exposure"),
        max_daily_loss=_positive_number(data.get("max_daily_loss"), "max_daily_loss"),
        max_capital_per_strategy=_positive_number(
            data.get("max_capital_per_strategy"), "max_capital_per_strategy"
        ),
        max_open_positions=max_open,
        max_stock_exposure=_positive_number(data.get("max_stock_exposure"), "max_stock_exposure"),
        max_sector_exposure_pct=_fraction(
            data.get("max_sector_exposure_pct"), "max_sector_exposure_pct"
        ),
        max_correlated_exposure_pct=_fraction(
            data.get("max_correlated_exposure_pct"), "max_correlated_exposure_pct"
        ),
        correlation_groups=tuple(groups),
        conflict_mode=mode,
        strategy_priorities=priorities,
        warn_at_pct_of_limit=warn,
    )


def default_policy() -> PortfolioPolicy:
    """No limits, conflicts rejected. The pre-policy behaviour, stated."""
    return PortfolioPolicy()
