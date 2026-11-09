"""Instrument domain models."""

from __future__ import annotations

import math
from datetime import date

from pydantic import BaseModel, ConfigDict, computed_field, field_validator, model_validator

from forward.core.enums import AssetClass, OptionExpiryType, OptionType


class Instrument(BaseModel):
    """A tradable contract used by the engine.

    ``conid`` stores a broker's contract identifier. ``symbol`` is the key used
    inside the engine.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    asset_class: AssetClass = AssetClass.EQUITY
    currency: str = "USD"
    exchange: str = "SMART"
    primary_exchange: str | None = None
    multiplier: float = 1.0
    tick_size: float = 0.01
    min_quantity: float = 1.0
    quantity_step: float = 1.0

    # Derivatives only
    expiry: date | None = None
    strike: float | None = None
    option_type: OptionType | None = None
    expiry_type: OptionExpiryType | None = None
    underlying: str | None = None

    # Broker identifiers
    conid: int | None = None
    local_symbol: str | None = None
    trading_class: str | None = None

    # Margin requirement per unit (F&O). For equities this is Reg-T margin.
    initial_margin_per_unit: float = 0.0
    maintenance_margin_per_unit: float = 0.0

    @model_validator(mode="after")
    def require_option_contract_metadata(self) -> Instrument:
        """Require explicit option expiry classification and contract details."""
        if self.asset_class is AssetClass.OPTION and any(
            value is None
            for value in (
                self.expiry,
                self.expiry_type,
                self.strike,
                self.option_type,
                self.underlying,
            )
        ):
            raise ValueError("option contracts require expiry type, expiry, strike, right, and underlying")
        return self

    @field_validator("symbol")
    @classmethod
    def _upper_symbol(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("multiplier", "tick_size", "min_quantity", "quantity_step")
    @classmethod
    def _positive_finite_contract_value(cls, value: float) -> float:
        """Require finite, positive contract and price-step values.

        Args:
            value: Contract multiplier, tick size, minimum quantity, or step.

        Returns:
            The validated value.

        Raises:
            ValueError: If the value is not finite and positive.
        """
        if not math.isfinite(value) or value <= 0:
            raise ValueError("instrument value must be finite and positive")
        return value

    @field_validator("strike")
    @classmethod
    def _positive_finite_strike(cls, value: float | None) -> float | None:
        """Require a finite positive strike when a strike is set.

        Args:
            value: Optional option strike.

        Returns:
            The validated strike or ``None``.

        Raises:
            ValueError: If a supplied strike is not finite and positive.
        """
        if value is not None and (not math.isfinite(value) or value <= 0):
            raise ValueError("instrument strike must be finite and positive")
        return value

    @field_validator("initial_margin_per_unit", "maintenance_margin_per_unit")
    @classmethod
    def _finite_nonnegative_margin(cls, value: float) -> float:
        """Require finite, non-negative margin values.

        Args:
            value: Margin per unit, where zero can mean no supplied value.

        Returns:
            The validated margin value.

        Raises:
            ValueError: If the value is negative or not finite.
        """
        if not math.isfinite(value) or value < 0:
            raise ValueError("instrument margin must be finite and non-negative")
        return value

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_derivative(self) -> bool:
        return self.asset_class in (AssetClass.FUTURE, AssetClass.OPTION)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_option(self) -> bool:
        return self.asset_class is AssetClass.OPTION

    @property
    def notional_multiplier(self) -> float:
        """Cash exposure of a single unit. Options/futures use the contract
        multiplier; a 100x equity option means 1 unit == 100 shares."""
        return self.multiplier

    def key(self) -> str:
        return self.symbol

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        if self.is_option:
            return f"{self.symbol} {self.expiry} {self.strike}{self.option_type}"
        return self.symbol
