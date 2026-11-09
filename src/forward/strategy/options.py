"""Configuration models and contract selection for multi-leg option strategies.

Option strategies are stored as data and interpreted by shared backtest and paper
execution services. Adding another strategy should therefore require a definition,
not another strategy class.
"""

from __future__ import annotations

from datetime import date
from math import isfinite
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from forward.core.enums import OptionType, OrderStatus
from forward.core.instrument_models import Instrument
from forward.core.order_models import Order
from forward.core.strategy_kinds import OPTIONS_STRATEGY_KIND
from forward.strategy.base import Strategy, StrategyContext, StrategyInputUnavailable

OPTIONS_STRATEGY_ENGINE_KEY = "configured_options"


class OptionLegRule(BaseModel):
    """One option leg, expressed relative to an observed underlying price.

    Attributes:
        side: Whether the simulated or live-paper order buys or sells the option.
        option_type: Call or put contract right.
        strike_offset: Offset from the underlying price used for strike selection.
        lots: Number of contract lots to trade.
    """

    model_config = ConfigDict(extra="forbid")

    side: Literal["BUY", "SELL"]
    option_type: OptionType
    strike_offset: float
    lots: int = Field(gt=0)

    @field_validator("strike_offset")
    @classmethod
    def validate_strike_offset(cls, value: float) -> float:
        """Reject non-finite strike offsets before saving an immutable version."""
        if not isfinite(value):
            raise ValueError("strike_offset must be finite")
        return value


class OptionStageRule(BaseModel):
    """A named group of option legs sharing an expiry selection.

    Attributes:
        name: Stable human-readable label attached to simulated orders.
        expiry: Nearest eligible weekly or monthly contract expiry.
        exit_all_on_expiry: Whether to close every open leg when this expiry arrives.
        legs: Legs submitted as one configured stage.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    expiry: Literal["nearest_monthly", "nearest_weekly"]
    exit_all_on_expiry: bool
    legs: list[OptionLegRule] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_legs(self) -> OptionStageRule:
        """Prevent duplicate legs that resolve to the same contract and side."""
        signatures = {(leg.side, leg.option_type, leg.strike_offset) for leg in self.legs}
        if len(signatures) != len(self.legs):
            raise ValueError("duplicate option legs are not allowed within a stage")
        return self


class OptionsStrategyDefinition(BaseModel):
    """Reusable configuration for a staged, multi-leg options strategy.

    Attributes:
        underlying: Contract-master underlying name.
        spot_symbol: Index or cash instrument used to observe the underlying.
        spot_exchange: Exchange segment for the spot observation.
        exchange: Derivative exchange segment containing the option contracts.
        stages: Ordered expiry and option-leg groups.
        max_loss: Absolute combined-P&L loss that stops and flattens the paper run.
        profit_exit: Whether positive P&L is left for manual exit or uses a target.
        profit_target: Combined-P&L target, required when ``profit_exit`` is target.
    """

    model_config = ConfigDict(extra="forbid")

    underlying: str = Field(min_length=1, max_length=64)
    spot_symbol: str = Field(min_length=1, max_length=64)
    spot_exchange: str = Field(min_length=1, max_length=16)
    exchange: str = Field(min_length=1, max_length=16)
    stages: list[OptionStageRule] = Field(min_length=1)
    max_loss: float = Field(gt=0)
    profit_exit: Literal["manual", "target"]
    profit_target: float | None = Field(gt=0)

    @field_validator("underlying", "spot_symbol", "spot_exchange", "exchange")
    @classmethod
    def normalize_identifiers(cls, value: str) -> str:
        """Trim and uppercase symbols and exchange identifiers."""
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("underlying, spot symbol and exchanges cannot be blank")
        return normalized

    @model_validator(mode="after")
    def validate_definition(self) -> OptionsStrategyDefinition:
        """Require finite risk limits and a complete, uniquely named stage plan."""
        if not isfinite(self.max_loss):
            raise ValueError("max_loss must be finite")
        if self.profit_exit == "target" and self.profit_target is None:
            raise ValueError("profit_target is required when profit_exit is target")
        if self.profit_exit == "manual" and self.profit_target is not None:
            raise ValueError("profit_target must be empty when profit_exit is manual")
        names = [stage.name for stage in self.stages]
        if len(names) != len(set(names)):
            raise ValueError("stage names must be unique")
        return self


class ConfiguredOptionsStrategy(Strategy):
    """Run a staged multi-leg strategy defined by saved configuration data.

    Contract selection occurs when each stage starts, using the latest spot and
    the active runner's exchange contract catalog. The same class runs in the
    historical simulator and in the live-data paper runner.
    """

    name = OPTIONS_STRATEGY_ENGINE_KEY

    def __init__(self, config: dict[str, object]) -> None:
        parsed = OptionsStrategyDefinition.model_validate(config)
        super().__init__(config=parsed.model_dump(mode="json"))
        self.config = parsed
        self._stage_index = 0
        self._stage_legs: list[tuple[Instrument, OptionLegRule]] | None = None
        self._stage_orders_submitted = False
        self._starting_equity: float | None = None
        self._expiry_exit: date | None = None
        self._stopping = False

    def on_start(self, ctx: StrategyContext) -> None:
        """Record the baseline used for the combined-P&L loss rule."""
        self._starting_equity = ctx.equity

    def on_bar(self, ctx: StrategyContext) -> None:
        """Select stages from current spot, submit paper/backtest orders, and monitor P&L."""
        if self._check_exit(ctx) or ctx.now is None or self._check_expiry_exit(ctx):
            return
        if self._stage_index >= len(self.config.stages):
            return
        if self._stage_legs is None:
            self._select_stage(ctx, self.config.stages[self._stage_index])
            return
        if not self._stage_orders_submitted:
            self._submit_stage(ctx)
            return
        if self._stage_filled(ctx):
            self._stage_index += 1
            self._stage_legs = None
            self._stage_orders_submitted = False

    def _select_stage(self, ctx: StrategyContext, stage: OptionStageRule) -> None:
        """Resolve the next stage against current spot and contract metadata."""
        catalog = ctx.contracts
        if catalog is None:
            raise RuntimeError("the active runner has no options contract catalog")
        if ctx.has_quote_source:
            spot_quote = ctx.quote(self.config.spot_symbol)
            if spot_quote is None:
                raise StrategyInputUnavailable(
                    f"live quote is missing for {self.config.spot_symbol}"
                )
            spot_value = getattr(spot_quote, "last", None)
        else:
            spot_value = ctx.price(self.config.spot_symbol)
        if spot_value is None:
            raise StrategyInputUnavailable(
                f"current price is missing for {self.config.spot_symbol}"
            )
        spot = float(spot_value)
        if not isfinite(spot) or spot <= 0:
            raise StrategyInputUnavailable(
                f"current price is invalid for {self.config.spot_symbol}: {spot_value!r}"
            )
        if ctx.now is None:
            raise RuntimeError("strategy context has no market timestamp")
        expiry_selector = {
            "nearest_monthly": catalog.nearest_monthly_expiry,
            "nearest_weekly": catalog.nearest_weekly_expiry,
        }[stage.expiry]
        expiry = expiry_selector(
            self.config.underlying,
            self.config.exchange,
            after=ctx.now.date(),
        )
        if stage.exit_all_on_expiry:
            self._expiry_exit = expiry
        selected = [
            (
                catalog.nearest_option(
                    self.config.underlying,
                    self.config.exchange,
                    expiry,
                    rule.option_type,
                    spot + rule.strike_offset,
                ),
                rule,
            )
            for rule in stage.legs
        ]
        for contract, _ in selected:
            ctx.watch_contract(contract)
        self._stage_legs = selected

    def _check_expiry_exit(self, ctx: StrategyContext) -> bool:
        """Close the strategy when its configured stage expiry is reached."""
        if self._expiry_exit is None or ctx.now is None or ctx.now.date() < self._expiry_exit:
            return False
        self._stopping = True
        ctx.close_all()
        if ctx.can_request_stop:
            ctx.request_stop()
        return True

    def _submit_stage(self, ctx: StrategyContext) -> None:
        """Submit a stage only once every selected contract has a current quote."""
        if self._stage_legs is None:
            return
        if not all(ctx.has_quote(contract.symbol) for contract, _ in self._stage_legs):
            return
        stage = self.config.stages[self._stage_index]
        orders = []
        for contract, rule in self._stage_legs:
            quantity = rule.lots * contract.quantity_step
            if rule.side == "SELL":
                quantity *= -1
            order = ctx.order(contract.symbol, quantity, tag=stage.name)
            if order is not None:
                orders.append(order)
            if order is not None and order.status is OrderStatus.REJECTED:
                self._stop_after_rejection(ctx, orders)
                return
        self._stage_orders_submitted = bool(orders)

    def _stage_filled(self, ctx: StrategyContext) -> bool:
        """Check that each current-stage leg reached its configured signed size."""
        if self._stage_legs is None:
            return False
        for contract, rule in self._stage_legs:
            direction = -1 if rule.side == "SELL" else 1
            target = direction * rule.lots * contract.quantity_step
            if abs(ctx.position(contract.symbol).quantity - target) > 1e-9:
                return False
        return True

    def _check_exit(self, ctx: StrategyContext) -> bool:
        """Apply the configured loss cap and optional profit target."""
        if self._stopping:
            return True
        if self._starting_equity is None:
            raise RuntimeError("strategy on_start must run before P&L checks")
        pnl = ctx.equity - self._starting_equity
        loss_hit = pnl <= -self.config.max_loss
        profit_hit = (
            self.config.profit_exit == "target"
            and self.config.profit_target is not None
            and pnl >= self.config.profit_target
        )
        if not (loss_hit or profit_hit):
            return False
        self._stopping = True
        ctx.close_all()
        if ctx.can_request_stop:
            ctx.request_stop()
        return True

    def _stop_after_rejection(
        self, ctx: StrategyContext, submitted_orders: list[Order]
    ) -> None:
        """Cancel active entry orders and flatten filled legs after a rejection.

        Args:
            ctx: Strategy access to runner capabilities.
            submitted_orders: Earlier orders accepted during this stage.
        """
        self._stopping = True
        for order in submitted_orders:
            if order.is_active:
                ctx.cancel(order, "another option leg was rejected")
        ctx.close_all()
        if ctx.can_request_stop:
            ctx.request_stop()


def options_definition(raw: object) -> OptionsStrategyDefinition:
    """Validate and return the options portion of a saved strategy version.

    Args:
        raw: Stored strategy definition object.

    Returns:
        A validated options strategy configuration.

    Raises:
        ValueError: If the definition does not declare the options engine or is
            structurally invalid.
    """
    if not isinstance(raw, dict) or raw.get("kind") != OPTIONS_STRATEGY_KIND:
        raise ValueError("strategy definition kind must be 'options'")
    config = raw.get("options")
    if not isinstance(config, dict):
        raise ValueError("strategy definition must contain an options object")
    return OptionsStrategyDefinition.model_validate(config)
