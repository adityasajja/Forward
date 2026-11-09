"""Risk state: the kill switch, the execution mode, and the limits.

Everything here used to be an in-process dict in ``forward/api/main.py``. That had
two consequences worth naming, because both are safety failures rather than
cosmetic ones:

* **A restart re-armed trading.** The kill switch and the paper/live mode lived in
  memory, so bouncing the process silently cleared a decision an operator had made
  *because something was wrong*.
* **There was one reader per route.** Each endpoint looked at the dict and decided
  for itself, so "is the kill switch engaged?" had as many answers as there were
  routes. The manual order route did not ask at all.

So the state is durable (``system_state``, a key/value table) and it is read
through one service. The gate the OMS uses is built from it, which is what makes
the kill switch apply to every order path rather than to the two that remembered.

The three transitions that can lose real money — engaging the kill switch,
releasing it, and going live — require a non-empty reason. That is deliberate
friction: the sentence is what shows up in the audit trail when someone asks why.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, time
from typing import Any

from forward.core.order_ports import OrderInstrumentLookupPort
from forward.core.risk_ports import (
    LiveRiskPolicyPort,
    RiskPortfolioPort,
    RiskStateStoragePort,
)
from forward.core.time import utcnow
from forward.execution.risk import RiskLimits

logger = logging.getLogger("forward.services.risk")

#: Keys in ``system_state``. Namespaced so a future key cannot collide with these.
KEY_KILL_SWITCH = "risk.kill_switch"
KEY_EXECUTION_MODE = "risk.execution_mode"
KEY_MODE_META = "risk.execution_mode.meta"
KEY_LIMITS = "risk.limits"

MODES = ("paper", "live")

_DEFAULT_STATE: dict[str, Any] = {
    KEY_KILL_SWITCH: False,
    KEY_EXECUTION_MODE: "paper",
    KEY_MODE_META: {},
    KEY_LIMITS: {},
}

#: Limits that may be set through the API. Anything not listed is not configurable
#: from a request body — a route that accepted arbitrary ``RiskLimits`` fields
#: would let a caller relax a limit the product does not intend to expose.
CONFIGURABLE_LIMITS: tuple[str, ...] = (
    "max_gross_exposure",
    "max_position_notional",
    "max_position_per_symbol",
    "max_daily_loss",
    "max_daily_trades",
    "max_open_positions",
    "max_order_notional",
    "allow_short",
    "allowed_symbols",
    "trading_window",
)
_OPTIONAL_LIMITS = frozenset(
    {
        "max_gross_exposure",
        "max_position_per_symbol",
        "max_daily_loss",
        "max_daily_trades",
        "max_open_positions",
        "max_order_notional",
        "allowed_symbols",
        "trading_window",
        "max_position_notional",
    }
)


class RiskStateError(Exception):
    """A risk-state change was refused. Carries the code the API returns."""

    def __init__(self, message: str, *, code: str = "risk_state_error", status: int = 400):
        self.code = code
        self.status = status
        super().__init__(message)


@dataclass(frozen=True)
class RiskStateSnapshot:
    """Risk controls plus the live protections for the active environment.

    The snapshot stores the selected policy values so formatting it never reads
    configuration or reaches outside the application layer.
    """

    kill_switch: bool
    execution_mode: str
    live_default_max_order_notional: float
    enforce_market_hours: bool
    changed_at: str | None = None
    changed_by: str | None = None
    reason: str | None = None
    limits: RiskLimits = field(default_factory=RiskLimits)

    @property
    def live(self) -> bool:
        return self.execution_mode == "live"

    def live_protections(self) -> dict[str, Any]:
        """What guards a LIVE order beyond the limits the operator set, and what is still unset.

        ``defaults_applied`` are limits the platform imposes because none was configured, so the
        interface can show the configured cap instead of making a person assume there is no cap.
        ``warnings`` are things a careful desk would want set
        before real orders: they do not block, they are shown.

        Returns:
            The default caps, missing safety limits, and market-hours setting.
        """
        applied: dict[str, float] = {}
        cap = self.limits.max_order_notional
        default_cap = self.live_default_max_order_notional
        if default_cap > 0 and (cap is None or cap == float("inf")):
            applied["max_order_notional"] = default_cap
        warnings: list[str] = []
        if self.limits.max_daily_loss is None:
            warnings.append("no_daily_loss_limit")
        return {
            "defaults_applied": applied,
            "warnings": warnings,
            "market_hours_enforced": self.enforce_market_hours,
        }

    def as_dict(self) -> dict[str, Any]:
        """Return the shape read by the dashboard.

        Returns:
            Risk state with flattened limits and live-order protections.
        """
        return {
            "kill_switch": self.kill_switch,
            "execution_mode": self.execution_mode,
            "live": self.live,
            "paper": not self.live,
            "changed_at": self.changed_at,
            "changed_by": self.changed_by,
            "reason": self.reason,
            "limits": _limits_as_dict(self.limits),
            "live_protections": self.live_protections(),
        }


#: NSE cash session, with the pre-open that orders may be entered in.
_ORDER_WINDOW = (time(9, 0), time(15, 30))


def nse_accepting_orders(now: datetime) -> bool:
    """True on an NSE trading day between 09:00 and 15:30 IST."""
    from forward.market_calendar import IST, NSEMarketCalendar

    local = now.astimezone(IST) if now.tzinfo else now.replace(tzinfo=IST)
    if not NSEMarketCalendar().is_trading_day(local.date()):
        return False
    return _ORDER_WINDOW[0] <= local.time() <= _ORDER_WINDOW[1]


@dataclass
class LiveGuards:
    """Checks that apply to LIVE orders only, in front of the limits gate.

    Paper orders pass straight through: a practice order outside market hours harms nobody.
    """

    inner: Any
    enforce_market_hours: bool = True
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))

    def __call__(self, draft: Any) -> Any:
        from forward.execution.oms import RiskDecision

        live = str(getattr(draft, "mode", "")).upper() == "LIVE"
        if live and self.enforce_market_hours and not nse_accepting_orders(self.now()):
            return RiskDecision.reject(
                "the market is closed: NSE takes orders on trading days, 09:00 to 15:30 IST",
                "market_closed",
            )
        return self.inner(draft)

    def __getattr__(self, name: str) -> Any:
        # Callers reach through to the gate's engine/portfolio; the wrapper must not hide them.
        if name == "inner":
            raise AttributeError(name)
        return getattr(self.inner, name)


def _limits_as_dict(limits: RiskLimits) -> dict[str, Any]:
    """Convert configured risk limits to JSON-safe values.

    Args:
        limits: Risk limits to serialize.

    Returns:
        A mapping with sets and time windows converted to JSON shapes.
    """
    out: dict[str, Any] = {}
    for name in CONFIGURABLE_LIMITS:
        value = getattr(limits, name, None)
        if isinstance(value, set):
            out[name] = sorted(value)
        elif isinstance(value, tuple):
            out[name] = [v.isoformat() if hasattr(v, "isoformat") else v for v in value]
        else:
            out[name] = value
    return out


def limits_from_dict(payload: dict[str, Any]) -> RiskLimits:
    """Rebuild :class:`RiskLimits` from stored JSON.

    Args:
        payload: Stored values keyed by configurable risk-limit name.

    Returns:
        Validated risk limits.

    Raises:
        TypeError: If a stored value has the wrong shape or type.
        ValueError: If a stored key or value is invalid.
    """
    if not isinstance(payload, dict):
        raise TypeError("risk limits must be stored as an object")
    if not payload:
        return RiskLimits()
    unknown = sorted(set(payload) - set(CONFIGURABLE_LIMITS))
    if unknown:
        raise ValueError(f"unknown risk limit(s): {', '.join(unknown)}")

    kwargs: dict[str, Any] = {}
    for key, value in payload.items():
        if value is None:
            if key in _OPTIONAL_LIMITS:
                continue
            raise ValueError(f"{key} cannot be empty")
        if key == "allowed_symbols":
            if not isinstance(value, (list, tuple, set)):
                raise TypeError("allowed_symbols must be a list of symbols")
            if any(not isinstance(symbol, str) or not symbol.strip() for symbol in value):
                raise ValueError("allowed_symbols must contain non-empty strings")
            kwargs[key] = {symbol.strip().upper() for symbol in value}
        elif key == "trading_window":
            kwargs[key] = _parse_window(value)
        elif key == "allow_short":
            if not isinstance(value, bool):
                raise TypeError("allow_short must be a boolean")
            kwargs[key] = value
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{key} must be a number")
            try:
                number = float(value)
            except OverflowError as error:
                raise ValueError(f"{key} must be finite and non-negative") from error
            if not math.isfinite(number) or number < 0:
                raise ValueError(f"{key} must be finite and non-negative")
            if key in {"max_daily_trades", "max_open_positions"}:
                if not number.is_integer():
                    raise ValueError(f"{key} must be a whole number")
                kwargs[key] = int(number)
            else:
                kwargs[key] = number
    return RiskLimits(**kwargs)


def _parse_window(value: Any) -> tuple[time, time]:
    """Parse a two-item trading window and reject invalid input.

    Args:
        value: Start and end time strings.

    Returns:
        The start and end times.

    Raises:
        TypeError: If the value is not a two-item list or tuple.
        ValueError: If a time is invalid or the end comes before the start.
    """
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise TypeError("trading_window must contain a start and end time")
    parsed = tuple(time.fromisoformat(item) for item in value)
    if any(item.tzinfo is not None for item in parsed):
        raise ValueError("trading_window times must not include a timezone")
    if parsed[0] > parsed[1]:
        raise ValueError("trading_window end must be at or after its start")
    return parsed


@dataclass
class RiskStateService:
    """Reads and writes the durable risk state.

    Every write that changes what the platform will do to real money demands a
    reason, and every write records the actor. The *service* enforces that rather
    than the route, so a second caller cannot skip it.

    The live-order policy is injected by the application composition boundary.
    """

    storage: RiskStateStoragePort
    live_policy: LiveRiskPolicyPort

    # ------------------------------------------------------------------ reads
    def snapshot(self) -> RiskStateSnapshot:
        """Return a snapshot of the current risk controls.

        Returns:
            The kill switch, execution mode, limits, and latest mode-change data.
        """
        return _snapshot_from_state(self.storage.read(_DEFAULT_STATE), self.live_policy)

    def limits(self) -> RiskLimits:
        return self.snapshot().limits

    def limits_with_daily_loss_cap(self, max_daily_loss: float) -> RiskLimits:
        """Apply a strategy loss cap to the current platform risk limits.

        Args:
            max_daily_loss: Maximum loss allowed by the strategy definition.

        Returns:
            Current limits with the tighter daily loss cap and kill switch.
        """
        snapshot = self.snapshot()
        limits = snapshot.limits
        configured_loss = limits.max_daily_loss
        limits.max_daily_loss = (
            max_daily_loss
            if configured_loss is None
            else min(float(configured_loss), max_daily_loss)
        )
        limits.kill_switch = snapshot.kill_switch
        return limits

    def kill_switch_engaged(self) -> bool:
        return self.snapshot().kill_switch

    def execution_mode(self) -> str:
        return self.snapshot().execution_mode

    @staticmethod
    def configurable_limits() -> list[str]:
        """Return the risk limit names that callers may configure.

        Returns:
            Configurable risk-limit names in the platform's declared order.
        """
        return list(CONFIGURABLE_LIMITS)

    # ----------------------------------------------------------------- writes
    def set_kill_switch(self, engaged: bool, *, reason: str, actor: str) -> RiskStateSnapshot:
        """Engage or release the global kill switch.

        Both directions require a reason. Releasing it is arguably the more
        consequential of the two — it re-enables trading — and a release with no
        recorded reason is indistinguishable from someone clearing it by accident.

        Args:
            engaged: Whether to block new orders.
            reason: Why the kill switch is changing.
            actor: User or service making the change.

        Returns:
            The risk controls after the change.
        """
        reason = (reason or "").strip()
        if not reason:
            raise RiskStateError(
                "changing the kill switch requires a reason", code="reason_required"
            )
        previous, current = self.storage.update(
            _DEFAULT_STATE,
            lambda _: {KEY_KILL_SWITCH: bool(engaged)},
            actor=actor,
            reason=reason,
        )
        if bool(previous[KEY_KILL_SWITCH]) != bool(engaged):
            logger.warning(
                "kill switch %s by %s (%s)",
                "ENGAGED" if engaged else "RELEASED",
                actor,
                reason,
            )
        return _snapshot_from_state(current, self.live_policy)

    def set_execution_mode(self, mode: str, *, reason: str, actor: str) -> RiskStateSnapshot:
        """Switch between ``paper`` and ``live``.

        Going live requires a reason; coming back to paper does not, because
        reducing risk should never be harder than taking it. The asymmetry is
        deliberate.

        Args:
            mode: New execution mode, either ``paper`` or ``live``.
            reason: Why the execution mode is changing.
            actor: User or service making the change.

        Returns:
            The risk controls after the change.
        """
        mode = (mode or "").strip().lower()
        if mode not in MODES:
            raise RiskStateError(f"mode must be one of {list(MODES)}", code="bad_mode")
        reason = (reason or "").strip()
        if mode == "live" and not reason:
            raise RiskStateError(
                "switching to live requires a reason — it is recorded in the audit trail",
                code="reason_required",
            )
        previous, current = self.storage.update(
            _DEFAULT_STATE,
            lambda state: {
                KEY_EXECUTION_MODE: mode,
                KEY_MODE_META: {
                    "changed_at": utcnow().isoformat(timespec="seconds") + "Z",
                    "changed_by": actor,
                    "reason": reason or None,
                    "previous": str(state[KEY_EXECUTION_MODE]),
                },
            },
            actor=actor,
            reason=reason or None,
        )
        previous_mode = str(previous[KEY_EXECUTION_MODE])
        if previous_mode != mode:
            logger.warning(
                "execution mode %s -> %s by %s (%s)",
                previous_mode,
                mode,
                actor,
                reason or "no reason",
            )
        return _snapshot_from_state(current, self.live_policy)

    def set_limits(self, payload: dict[str, Any], *, actor: str) -> RiskStateSnapshot:
        """Replace the configured limits.

        Wholesale replacement rather than a merge, so "set this limit back to
        unlimited" is expressible — a merge cannot distinguish "absent" from
        "cleared".

        Args:
            payload: Complete set of supported limit values.
            actor: User or service making the change.

        Returns:
            The risk controls after the change.
        """
        unknown = sorted(set(payload) - set(CONFIGURABLE_LIMITS))
        if unknown:
            raise RiskStateError(f"unknown limit(s): {', '.join(unknown)}", code="unknown_limit")
        try:
            # Construct once to validate before storing: a limit set that cannot
            # be rebuilt would poison every later read of the risk state.
            candidate = limits_from_dict(payload)
        except (TypeError, ValueError) as exc:
            raise RiskStateError(f"invalid limits: {exc}", code="invalid_limit") from exc
        _, current = self.storage.update(
            _DEFAULT_STATE,
            lambda _: {KEY_LIMITS: _limits_as_dict(candidate)},
            actor=actor,
            reason=None,
        )
        return _snapshot_from_state(current, self.live_policy)

    # ------------------------------------------------------------------- gate
    def gate(
        self,
        *,
        portfolio: RiskPortfolioPort,
        instruments: OrderInstrumentLookupPort,
        now: Callable[[], datetime] | None = None,
    ) -> Any:
        """Build the OMS risk gate from the current state.

        Returns a :class:`~forward.execution.oms.LimitsRiskGate` over a
        :class:`~forward.execution.risk.RiskEngine` whose ``kill_switch`` is the
        durable flag. This is the single place the kill switch enters the order
        path, which is what makes it apply to every route instead of the two that
        remembered to ask.

        ``portfolio`` and ``instruments`` are injected because only the caller
        knows whether it is the paper engine or the live broker asking.

        Args:
            portfolio: Current portfolio used to check exposure limits.
            instruments: Instrument lookup used to validate symbols and values.
            now: Optional clock used by the market-hours rule.

        Returns:
            A gate that applies the current risk controls to orders.
        """
        from forward.execution.oms import LimitsRiskGate
        from forward.execution.risk import RiskEngine

        snapshot = self.snapshot()
        limits = snapshot.limits
        # The kill switch is read at gate-construction time; the OMS builds a gate
        # per request, so engaging it takes effect on the next order.
        limits.kill_switch = snapshot.kill_switch
        # In live mode a missing order cap is not "no cap": a mistyped quantity
        # must not be unbounded.
        if snapshot.live:
            for name, value in snapshot.live_protections()["defaults_applied"].items():
                setattr(limits, name, value)
        gate = LimitsRiskGate(
            engine=RiskEngine(limits=limits),
            portfolio=portfolio,
            instruments=instruments,
        )
        return LiveGuards(
            inner=gate,
            enforce_market_hours=snapshot.enforce_market_hours,
            **({"now": now} if now else {}),
        )


def _snapshot_from_state(
    values: dict[str, Any], live_policy: LiveRiskPolicyPort
) -> RiskStateSnapshot:
    """Build the public risk snapshot from stored state values.

    Args:
        values: State values returned by the storage port.
        live_policy: Live-order protections selected by the application environment.

    Returns:
        A typed snapshot of the current risk controls.
    """
    meta = values[KEY_MODE_META]
    if not isinstance(meta, dict):
        raise TypeError("risk mode metadata must be stored as an object")
    kill_switch = values[KEY_KILL_SWITCH]
    if not isinstance(kill_switch, bool):
        raise TypeError("kill switch state must be stored as a boolean")
    execution_mode = values[KEY_EXECUTION_MODE]
    if execution_mode not in MODES:
        raise ValueError(f"stored execution mode must be one of {list(MODES)}")
    limits = values[KEY_LIMITS]
    if not isinstance(limits, dict):
        raise TypeError("stored risk limits must be an object")
    return RiskStateSnapshot(
        kill_switch=kill_switch,
        execution_mode=execution_mode,
        changed_at=meta.get("changed_at"),
        changed_by=meta.get("changed_by"),
        reason=meta.get("reason"),
        limits=limits_from_dict(limits),
        live_default_max_order_notional=float(live_policy.live_default_max_order_notional),
        enforce_market_hours=live_policy.enforce_market_hours,
    )


__all__ = [
    "CONFIGURABLE_LIMITS",
    "KEY_EXECUTION_MODE",
    "KEY_KILL_SWITCH",
    "KEY_LIMITS",
    "KEY_MODE_META",
    "MODES",
    "RiskStateError",
    "RiskStateService",
    "RiskStateSnapshot",
    "limits_from_dict",
]
