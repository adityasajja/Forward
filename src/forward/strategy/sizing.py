"""Position Sizing & Risk Budgeting engine for cash-equity strategies.

The enforced signal-to-execution pipeline is:

    SIGNAL
    → POSITION SIZE
    → STRATEGY RISK
    → PORTFOLIO RISK
    → OMS

This module owns deterministic position sizing and risk budgeting calculations
across Backtest, Paper, and Live execution paths. It does NOT make autonomous
optimization decisions, dynamic capital reallocations, or AI sizing choices.
Downstream RiskEngine and PortfolioRiskGate remain strictly authoritative.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class SizingMethod(str, Enum):
    """Supported position sizing methods."""

    FIXED_QUANTITY = "FIXED_QUANTITY"
    FIXED_RUPEE_VALUE = "FIXED_RUPEE_VALUE"
    PERCENT_OF_CAPITAL = "PERCENT_OF_CAPITAL"
    PERCENT_OF_AVAILABLE_CAPITAL = "PERCENT_OF_AVAILABLE_CAPITAL"
    RISK_PER_TRADE = "RISK_PER_TRADE"
    ATR_VOLATILITY_SIZING = "ATR_VOLATILITY_SIZING"


class RoundingRule(str, Enum):
    """Quantity rounding behaviors."""

    FLOOR = "FLOOR"  # Default: round down to avoid breaching risk/capital
    ROUND = "ROUND"  # Nearest integer
    CEIL = "CEIL"    # Round up


@dataclass(frozen=True)
class SizingConfig:
    """Strategy or deployment position sizing configuration.

    Attributes:
        method: Position sizing method.
        rounding_rule: Rule used to round the final order quantity.
        available_capital_fraction: Maximum share of available cash used by one
            order, from greater than zero through one.
    """

    method: SizingMethod
    rounding_rule: RoundingRule
    available_capital_fraction: float
    risk_per_trade_pct: float | None = None
    risk_per_trade_rupees: float | None = None
    fixed_quantity: float | None = None
    fixed_rupee_value: float | None = None
    capital_fraction: float | None = None
    max_quantity: float | None = None
    max_position_value: float | None = None
    max_portfolio_exposure_fraction: float | None = None
    min_quantity: float | None = None
    atr_multiplier: float | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> SizingConfig:
        """Build sizing settings from a complete, checked configuration.

        Args:
            data: JSON object from a saved strategy definition.

        Returns:
            The validated sizing settings.

        Raises:
            TypeError: If the input is not an object or a value has the wrong type.
            ValueError: If the sizing method, rounding rule, or value is invalid.
        """
        if not isinstance(data, dict):
            raise TypeError("sizing must be a configuration object")

        known_fields = {
            "method",
            "risk_per_trade_pct",
            "risk_per_trade_rupees",
            "fixed_quantity",
            "fixed_rupee_value",
            "capital_fraction",
            "max_quantity",
            "max_position_value",
            "max_portfolio_exposure_fraction",
            "min_quantity",
            "rounding_rule",
            "available_capital_fraction",
            "atr_multiplier",
        }
        unknown_fields = sorted(set(data) - known_fields)
        if unknown_fields:
            raise ValueError(f"unknown sizing fields: {', '.join(unknown_fields)}")

        method_value = data.get("method")
        if not isinstance(method_value, str) or not method_value.strip():
            raise ValueError("sizing.method is required")
        try:
            method = SizingMethod(method_value.upper())
        except ValueError as exc:
            raise ValueError(f"unknown sizing method: {method_value!r}") from exc

        rounding_value = data.get("rounding_rule")
        if not isinstance(rounding_value, str) or not rounding_value.strip():
            raise ValueError("sizing.rounding_rule is required")
        try:
            rounding = RoundingRule(rounding_value.upper())
        except ValueError as exc:
            raise ValueError(f"unknown rounding rule: {rounding_value!r}") from exc

        available_fraction = data.get("available_capital_fraction")
        if isinstance(available_fraction, bool) or not isinstance(available_fraction, (int, float)):
            raise TypeError("sizing.available_capital_fraction is required and must be a number")

        def number(field: str) -> float | None:
            value = data.get(field)
            if value is None:
                return None
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"sizing.{field} must be a number or null")
            result = float(value)
            if not math.isfinite(result):
                raise ValueError(f"sizing.{field} must be finite")
            return result

        config = cls(
            method=method,
            risk_per_trade_pct=number("risk_per_trade_pct"),
            risk_per_trade_rupees=number("risk_per_trade_rupees"),
            fixed_quantity=number("fixed_quantity"),
            fixed_rupee_value=number("fixed_rupee_value"),
            capital_fraction=number("capital_fraction"),
            max_quantity=number("max_quantity"),
            max_position_value=number("max_position_value"),
            max_portfolio_exposure_fraction=number("max_portfolio_exposure_fraction"),
            min_quantity=number("min_quantity"),
            rounding_rule=rounding,
            available_capital_fraction=float(available_fraction),
            atr_multiplier=number("atr_multiplier"),
        )
        config.validate()
        return config

    def validate(self) -> None:
        """Reject missing or invalid values for the selected sizing method.

        Raises:
            ValueError: If this method lacks a required setting or has an
                invalid numeric limit.
        """
        if not isinstance(self.method, SizingMethod):
            raise TypeError("sizing.method must be a SizingMethod")
        if not isinstance(self.rounding_rule, RoundingRule):
            raise TypeError("sizing.rounding_rule must be a RoundingRule")
        if (
            not math.isfinite(self.available_capital_fraction)
            or not 0 < self.available_capital_fraction <= 1
        ):
            raise ValueError("sizing.available_capital_fraction must be greater than 0 and at most 1")

        required_field = {
            SizingMethod.FIXED_QUANTITY: "fixed_quantity",
            SizingMethod.FIXED_RUPEE_VALUE: "fixed_rupee_value",
            SizingMethod.PERCENT_OF_CAPITAL: "capital_fraction",
            SizingMethod.PERCENT_OF_AVAILABLE_CAPITAL: "capital_fraction",
        }.get(self.method)
        if required_field and getattr(self, required_field) is None:
            raise ValueError(f"sizing.{required_field} is required for {self.method.value}")

        for field in (
            "risk_per_trade_pct",
            "risk_per_trade_rupees",
            "fixed_quantity",
            "fixed_rupee_value",
            "capital_fraction",
            "max_quantity",
            "max_position_value",
            "max_portfolio_exposure_fraction",
            "min_quantity",
            "atr_multiplier",
        ):
            value = getattr(self, field)
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError(f"sizing.{field} must be finite and non-negative")

        if (
            self.max_portfolio_exposure_fraction is not None
            and self.max_portfolio_exposure_fraction > 1
        ):
            raise ValueError(
                "sizing.max_portfolio_exposure_fraction must be at most 1"
            )

        if self.method is SizingMethod.RISK_PER_TRADE:
            budget_values = (self.risk_per_trade_pct, self.risk_per_trade_rupees)
            if sum(value is not None for value in budget_values) != 1:
                raise ValueError(
                    "RISK_PER_TRADE requires exactly one of risk_per_trade_pct "
                    "or risk_per_trade_rupees"
                )
        if self.method is SizingMethod.ATR_VOLATILITY_SIZING:
            if self.atr_multiplier is None:
                raise ValueError("sizing.atr_multiplier is required for ATR_VOLATILITY_SIZING")
            if sum(
                value is not None
                for value in (self.risk_per_trade_pct, self.risk_per_trade_rupees)
            ) != 1:
                raise ValueError(
                    "ATR_VOLATILITY_SIZING requires exactly one explicit risk budget"
                )

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["method"] = self.method.value
        d["rounding_rule"] = self.rounding_rule.value
        return d


@dataclass(frozen=True)
class SizingResult:
    """The outcome of position sizing calculation and budgeting preview."""

    method: str
    raw_quantity: float
    final_quantity: int
    entry_price: float
    stop_price: float | None
    risk_per_share: float | None
    risk_amount: float | None
    stop_distance: float | None
    atr: float | None
    atr_multiplier: float | None
    capital_allocated: float
    capital_available: float
    position_value: float
    portfolio_impact_pct: float
    sector_impact_pct: float | None
    remaining_capital: float
    capped_by: str | None
    cap_reasons: list[str]
    rejection_reason: str | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _risk_budget(config: SizingConfig, allocated_capital: float) -> float:
    """Return the configured rupee risk for one strategy position.

    Args:
        config: Position sizing settings.
        allocated_capital: Capital assigned to the strategy.

    Returns:
        The fixed rupee risk, or the percentage-based risk amount.
    """
    if config.risk_per_trade_rupees is not None:
        return config.risk_per_trade_rupees
    if config.risk_per_trade_pct is None or config.risk_per_trade_pct <= 0:
        raise ValueError("an explicit positive risk budget is required")
    percentage = config.risk_per_trade_pct
    fraction = percentage / 100.0
    return allocated_capital * fraction


class PositionSizingEngine:
    """Deterministic, unified Position Sizing Engine.

    Shared across Backtest, Paper, and Live order pathways.
    """

    @classmethod
    def calculate(
        cls,
        config: SizingConfig,
        *,
        entry_price: float,
        capital: float,
        available_capital: float,
        stop_price: float | None = None,
        stop_loss_pct: float | None = None,
        atr: float | None = None,
        multiplier: float,
        current_stock_exposure: float = 0.0,
        max_stock_exposure: float | None = None,
        current_portfolio_exposure: float = 0.0,
        max_total_portfolio_exposure: float | None = None,
        current_sector_exposure: float = 0.0,
        max_sector_exposure: float | None = None,
    ) -> SizingResult:
        """Calculate position size, enforce caps, and construct preview metrics."""
        config.validate()
        if not math.isfinite(capital) or capital < 0:
            raise ValueError("capital must be finite and non-negative")
        if not math.isfinite(available_capital) or available_capital < 0:
            raise ValueError("available_capital must be finite and non-negative")
        if not math.isfinite(multiplier) or multiplier <= 0:
            raise ValueError("multiplier must be finite and positive")
        cap_allocated = float(capital)
        cap_avail = float(available_capital)

        if entry_price <= 0 or not math.isfinite(entry_price):
            raise ValueError("entry_price must be finite and positive")

        # 1. Resolve risk per share and stop loss
        risk_per_share: float | None = None
        stop_distance: float | None = None
        resolved_stop_price: float | None = stop_price

        if resolved_stop_price is not None and resolved_stop_price > 0:
            stop_distance = abs(entry_price - resolved_stop_price)
            if stop_distance > 0:
                risk_per_share = stop_distance
        elif stop_loss_pct is not None and stop_loss_pct > 0:
            # stop_loss_pct given as percentage (e.g. 2.0 = 2%)
            fraction = stop_loss_pct / 100.0
            risk_per_share = entry_price * fraction
            stop_distance = risk_per_share
            resolved_stop_price = max(entry_price - risk_per_share, 0.0)

        # 2. Calculate raw quantity based on configured method.
        # All rupee notionals buy exposure, and one unit of quantity costs
        # ``entry_price * multiplier`` (100x for a NIFTY future). Dividing by
        # the bare price sizes derivatives ~multiplier-times too big.
        contract_mult = multiplier
        unit_price = entry_price * contract_mult
        raw_qty: float = 0.0
        calculated_risk_amount: float | None = None
        rejection_reason: str | None = None

        method = config.method

        if method == SizingMethod.FIXED_QUANTITY:
            raw_qty = float(config.fixed_quantity)

        elif method == SizingMethod.FIXED_RUPEE_VALUE:
            raw_qty = float(config.fixed_rupee_value) / unit_price

        elif method == SizingMethod.PERCENT_OF_CAPITAL:
            frac = float(config.capital_fraction)
            notional = cap_allocated * frac
            raw_qty = (notional / unit_price) if notional > 0 else 0.0

        elif method == SizingMethod.PERCENT_OF_AVAILABLE_CAPITAL:
            frac = float(config.capital_fraction)
            notional = cap_avail * frac
            raw_qty = (notional / unit_price) if notional > 0 else 0.0

        elif method == SizingMethod.RISK_PER_TRADE:
            calculated_risk_amount = _risk_budget(config, cap_allocated)

            if risk_per_share is None or risk_per_share <= 0:
                raise ValueError("RISK_PER_TRADE sizing requires a positive stop distance")
            raw_qty = calculated_risk_amount / (risk_per_share * contract_mult)

        elif method == SizingMethod.ATR_VOLATILITY_SIZING:
            calculated_risk_amount = _risk_budget(config, cap_allocated)

            atr_val = atr if (atr is not None and atr > 0) else None
            atr_multiple = config.atr_multiplier
            if atr_val is None:
                raise ValueError("ATR_VOLATILITY_SIZING requires a positive ATR value")
            risk_per_share = atr_val * float(atr_multiple)
            stop_distance = risk_per_share
            if resolved_stop_price is None:
                resolved_stop_price = max(entry_price - risk_per_share, 0.0)
            raw_qty = calculated_risk_amount / (risk_per_share * contract_mult)

        if raw_qty <= 0:
            return cls._empty_result(
                config,
                entry_price,
                cap_allocated,
                cap_avail,
                raw_quantity=raw_qty,
                stop_price=resolved_stop_price,
                risk_per_share=risk_per_share,
                risk_amount=calculated_risk_amount,
                stop_distance=stop_distance,
                atr=atr,
                rejection_reason=rejection_reason or "Calculated raw quantity is zero",
            )

        # 3. Apply capping hierarchy
        final_qty_float = raw_qty
        capped_by: str | None = None
        cap_reasons: list[str] = []

        # (max_quantity_value, label) — each cap is applied in order
        _caps: list[tuple[float | None, str]] = [
            (
                (cap_avail * config.available_capital_fraction) / unit_price
                if cap_avail > 0
                else 0.0,
                "Available Capital",
            ),
            (config.max_position_value / unit_price if config.max_position_value is not None else None, "Strategy Max Position Value"),
            (config.max_quantity if config.max_quantity is not None else None, "Strategy Max Quantity"),
            (
                (cap_allocated * config.max_portfolio_exposure_fraction) / unit_price
                if config.max_portfolio_exposure_fraction is not None
                else None,
                "Strategy Max Portfolio Exposure",
            ),
            (
                max(max_stock_exposure - current_stock_exposure, 0.0) / unit_price
                if max_stock_exposure is not None
                else None,
                "Stock Exposure Limit",
            ),
            (
                max(max_total_portfolio_exposure - current_portfolio_exposure, 0.0) / unit_price
                if max_total_portfolio_exposure is not None
                else None,
                "Portfolio Exposure Limit",
            ),
            (
                max(max_sector_exposure - current_sector_exposure, 0.0) / unit_price
                if max_sector_exposure is not None
                else None,
                "Sector Exposure Limit",
            ),
        ]

        for cap_value, label in _caps:
            if cap_value is not None and cap_value < final_qty_float:
                final_qty_float = cap_value
                capped_by = label
                cap_reasons.append(label)

        # 4. Rounding
        final_int: int = 0
        if config.rounding_rule == RoundingRule.ROUND:
            final_int = round(final_qty_float)
        elif config.rounding_rule == RoundingRule.CEIL:
            final_int = math.ceil(final_qty_float)
        else:
            final_int = math.floor(final_qty_float)

        final_int = max(final_int, 0)

        # 5. Enforce minimum quantity
        min_q = int(config.min_quantity) if config.min_quantity is not None else None
        if min_q is not None and final_int < min_q:
            if final_int > 0:
                cap_reasons.append("Below Minimum Quantity")
                rejection_reason = f"Final quantity ({final_int}) below minimum ({min_q})"
            final_int = 0

        # Derived metrics
        pos_value = final_int * unit_price
        port_impact_pct = (pos_value / cap_allocated * 100.0) if cap_allocated > 0 else 0.0
        sector_impact_pct = (
            ((current_sector_exposure + pos_value) / cap_allocated * 100.0)
            if cap_allocated > 0 and current_sector_exposure > 0
            else None
        )
        remaining_cap = max(cap_avail - pos_value, 0.0)

        return SizingResult(
            method=method.value,
            raw_quantity=round(raw_qty, 2),
            final_quantity=final_int,
            entry_price=round(entry_price, 2),
            stop_price=round(resolved_stop_price, 2) if resolved_stop_price is not None else None,
            risk_per_share=round(risk_per_share, 2) if risk_per_share is not None else None,
            risk_amount=round(calculated_risk_amount, 2) if calculated_risk_amount is not None else None,
            stop_distance=round(stop_distance, 2) if stop_distance is not None else None,
            atr=round(atr, 2) if atr is not None else None,
            atr_multiplier=config.atr_multiplier,
            capital_allocated=round(cap_allocated, 2),
            capital_available=round(cap_avail, 2),
            position_value=round(pos_value, 2),
            portfolio_impact_pct=round(port_impact_pct, 2),
            sector_impact_pct=round(sector_impact_pct, 2) if sector_impact_pct is not None else None,
            remaining_capital=round(remaining_cap, 2),
            capped_by=capped_by,
            cap_reasons=cap_reasons,
            rejection_reason=rejection_reason,
        )

    @classmethod
    def _empty_result(
        cls,
        config: SizingConfig,
        entry_price: float,
        capital_allocated: float,
        capital_available: float,
        raw_quantity: float = 0.0,
        stop_price: float | None = None,
        risk_per_share: float | None = None,
        risk_amount: float | None = None,
        stop_distance: float | None = None,
        atr: float | None = None,
        rejection_reason: str | None = None,
    ) -> SizingResult:
        return SizingResult(
            method=config.method.value,
            raw_quantity=round(raw_quantity, 2),
            final_quantity=0,
            entry_price=round(entry_price, 2) if entry_price > 0 else 0.0,
            stop_price=round(stop_price, 2) if stop_price is not None else None,
            risk_per_share=round(risk_per_share, 2) if risk_per_share is not None else None,
            risk_amount=round(risk_amount, 2) if risk_amount is not None else None,
            stop_distance=round(stop_distance, 2) if stop_distance is not None else None,
            atr=round(atr, 2) if atr is not None else None,
            atr_multiplier=config.atr_multiplier,
            capital_allocated=round(capital_allocated, 2),
            capital_available=round(capital_available, 2),
            position_value=0.0,
            portfolio_impact_pct=0.0,
            sector_impact_pct=None,
            remaining_capital=round(capital_available, 2),
            capped_by=None,
            cap_reasons=[],
            rejection_reason=rejection_reason,
        )
