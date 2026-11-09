"""Configuration for running saved paper deployments.

This module keeps deployment settings separate from the long-running runner
service. The runner, deployment use case, and health check share this model.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from forward.core.session import STRATEGY_TIMEFRAMES
from forward.strategy.definition import parse_definition

_CONFIG_FIELDS = {
    "symbols",
    "exchange",
    "symbol_exchanges",
    "interval_seconds",
    "lookback_days",
    "timeframe",
    "entry_cutoff",
    "square_off_time",
    "max_open_positions",
    "stop_loss_pct",
    "take_profit_pct",
    "sizing",
    "signal_basis",
}


@dataclass
class RunnerConfig:
    """One deployment's running parameters.

    Strategy rules stay in the saved strategy version. This object holds the
    settings needed to run that version.
    """

    symbols: tuple[str, ...]
    interval_seconds: float
    lookback_days: int
    exchange: str = "NSEEQ"
    #: Per-symbol exchange for a deployment that mixes exchange segments. A symbol
    #: not listed uses the required deployment-level exchange.
    symbol_exchanges: dict[str, str] = field(default_factory=dict)
    #: Platform cadence, not a strategy rule. A tick-driven loop would run the rules
    #: once per tick per symbol, which at ~10 ticks/s across 50 symbols is 500
    #: pandas evaluations a second for a signal that is only actionable once a
    #: bar. One pass a second is far finer than the daily rules can distinguish.
    #: Platform evaluation cadence, in seconds; saved strategies may override it.
    #: Platform history window for indicator warmup, in calendar days; saved
    #: strategies may override it.
    #: One of the daily or intraday strategy intervals.
    #: See `DeploymentLoop.timeframe_minutes` for what this changes.
    timeframe: str = "1d"
    entry_cutoff: str | None = None
    square_off_time: str | None = None
    #: Refuse to open more than this many open positions at once.
    max_open_positions: int = 10
    #: Trailing stop applied to every paper position, as the exit rules would.
    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None
    sizing: dict[str, Any] | None = None
    #: What daily rules read. Saved deployments must supply this explicitly.
    #: Direct construction uses ``closed_bar``: rules decide on completed daily
    #: bars and act at today's first price, as the backtest does. ``live`` reads a
    #: half-finished bar that can produce signals the backtest never sees.
    signal_basis: str = "closed_bar"

    @property
    def product(self) -> str:
        """Return the order product selected by the deployment session style.

        Returns:
            ``INTRADAY`` when the deployment has a square-off time, otherwise
            ``DELIVERY``.
        """
        return "INTRADAY" if self.square_off_time else "DELIVERY"

    def apply_context(self, definition: dict[str, Any]) -> None:
        """Apply timing, sizing, and position limits from a saved strategy.

        Args:
            definition: The saved strategy version.
        """
        from forward.core.session import validate_session

        params = definition.get("params") or {}
        if params.get("max_positions") is not None:
            self.max_open_positions = int(params["max_positions"])
        if isinstance(definition.get("sizing"), dict):
            self.sizing = definition["sizing"]
        context = definition.get("context") or {}
        if not context:
            return  # Legacy registry strategies retain their deployment cadence.
        timeframe = context.get("timeframe", "1d")
        if timeframe not in STRATEGY_TIMEFRAMES:
            raise ValueError(f"Deployment does not support {timeframe} bars")
        self.timeframe = timeframe
        self.signal_basis = "closed_bar"
        self.entry_cutoff = self.square_off_time = None
        if context.get("style") == "intraday":
            self.entry_cutoff = context.get("entry_cutoff", "14:45")
            self.square_off_time = context.get("square_off_time", "15:15")
            from forward.market_calendar import get_market_calendar

            calendar = get_market_calendar()
            validate_session(
                self.entry_cutoff,
                self.square_off_time,
                timeframe,
                market_open=calendar.market_open,
                market_close=calendar.market_close,
            )

    def entry_closed(self, now: datetime) -> bool:
        """Return whether the configured entry cutoff has passed.

        Args:
            now: Current market time.

        Returns:
            Whether new entries must stop.
        """
        from forward.core.session import clock, local_time
        return self.entry_cutoff is not None and local_time(now) >= clock(self.entry_cutoff)

    def square_off_due(self, now: datetime) -> bool:
        """Return whether the configured square-off time has passed.

        Args:
            now: Current market time.

        Returns:
            Whether open positions must be closed.
        """
        from forward.core.session import clock, local_time
        return self.square_off_time is not None and local_time(now) >= clock(self.square_off_time)

    def exchange_of(self, symbol: str) -> str:
        """Return the exchange configured for a symbol.

        Args:
            symbol: Trading symbol.

        Returns:
            The symbol-specific exchange or the deployment exchange.
        """
        return self.symbol_exchanges.get(symbol.upper()) or self.exchange

    @classmethod
    def from_deployment(
        cls,
        row: dict[str, Any],
        *,
        platform_interval_seconds: float,
        platform_lookback_days: int,
    ) -> RunnerConfig:
        """Build settings from one saved deployment row.

        ``config`` is stored on the row so a deployment's behaviour is
        reproducible from that row alone — the property that makes "why did this
        trade?" answerable a week later.

        ``config`` may arrive as its database JSON string or as a mapping from
        an already-decoded storage adapter. Required strategy settings are
        checked before any strategy-owned settings are applied. The runner's
        pass interval and history warmup use explicit platform configuration
        when the saved deployment does not override them.

        Args:
            row: Saved deployment, including its config and strategy definition.
            platform_interval_seconds: Required platform runner cadence.
            platform_lookback_days: Required history warmup window.

        Returns:
            The settings used by the paper runner.

        Raises:
            ValueError: If the row or saved configuration is incomplete or invalid.
        """
        if not isinstance(row, Mapping):
            raise ValueError("deployment row must be an object")
        if "config" not in row:
            raise ValueError("deployment config is required")
        raw = row["config"]
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError("deployment config must contain valid JSON") from exc
        if not isinstance(raw, Mapping):
            raise ValueError("deployment config must be an object")
        if any(not isinstance(name, str) for name in raw):
            raise ValueError("deployment config field names must be strings")
        unexpected = set(raw).difference(_CONFIG_FIELDS)
        if unexpected:
            raise ValueError(
                f"deployment config has unknown fields: {', '.join(sorted(unexpected))}"
            )

        symbols = raw.get("symbols")
        if not isinstance(symbols, list) or not symbols:
            raise ValueError("deployment config symbols must be a non-empty list")
        if any(not isinstance(symbol, str) or not symbol.strip() for symbol in symbols):
            raise ValueError("deployment config symbols must contain non-empty strings")
        normalized_symbols = tuple(symbol.strip().upper() for symbol in symbols)
        if len(set(normalized_symbols)) != len(normalized_symbols):
            raise ValueError("deployment config symbols must be unique")
        exchange = cls._required_string(raw, "exchange").upper()
        timeframe = cls._required_string(raw, "timeframe").lower()
        if timeframe not in STRATEGY_TIMEFRAMES:
            raise ValueError(f"unsupported deployment timeframe: {timeframe!r}")
        signal_basis = cls._required_string(raw, "signal_basis").lower()
        if signal_basis not in {"closed_bar", "live"}:
            raise ValueError(f"unsupported signal_basis: {signal_basis!r}")
        max_open_positions = cls._required_positive_integer(raw, "max_open_positions")

        symbol_exchanges = raw.get("symbol_exchanges", {})
        if not isinstance(symbol_exchanges, Mapping):
            raise ValueError("symbol_exchanges must be an object keyed by symbol")
        normalized_exchanges: dict[str, str] = {}
        for symbol, symbol_exchange in symbol_exchanges.items():
            if not isinstance(symbol, str) or not symbol.strip():
                raise ValueError("symbol_exchanges keys must be non-empty strings")
            if not isinstance(symbol_exchange, str) or not symbol_exchange.strip():
                raise ValueError("symbol_exchanges values must be non-empty strings")
            normalized_symbol = symbol.strip().upper()
            if normalized_symbol not in normalized_symbols:
                raise ValueError(
                    f"symbol exchange override {normalized_symbol!r} is not in symbols"
                )
            normalized_exchanges[normalized_symbol] = symbol_exchange.strip().upper()

        interval_seconds = cls._positive_number(
            raw, "interval_seconds", platform_interval_seconds
        )
        lookback_days = cls._positive_integer(
            raw, "lookback_days", platform_lookback_days
        )
        stop_loss_pct = cls._optional_positive_number(raw, "stop_loss_pct")
        take_profit_pct = cls._optional_positive_number(raw, "take_profit_pct")
        sizing = raw.get("sizing")
        if "sizing" in raw and not isinstance(sizing, Mapping):
            raise ValueError("sizing must be an object")
        entry_cutoff = cls._optional_string(raw, "entry_cutoff")
        square_off_time = cls._optional_string(raw, "square_off_time")
        if (entry_cutoff is None) != (square_off_time is None):
            raise ValueError("entry_cutoff and square_off_time must be supplied together")
        if entry_cutoff is not None and square_off_time is not None:
            from forward.core.session import validate_session
            from forward.market_calendar import get_market_calendar

            calendar = get_market_calendar()
            validate_session(
                entry_cutoff,
                square_off_time,
                timeframe,
                market_open=calendar.market_open,
                market_close=calendar.market_close,
            )
        result = cls(
            symbols=normalized_symbols,
            exchange=exchange,
            symbol_exchanges=normalized_exchanges,
            interval_seconds=interval_seconds,
            lookback_days=lookback_days,
            timeframe=timeframe,
            entry_cutoff=entry_cutoff,
            square_off_time=square_off_time,
            max_open_positions=max_open_positions,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            sizing=dict(sizing) if sizing is not None else None,
            signal_basis=signal_basis,
        )
        definition, _ = parse_definition(row.get("definition"))
        if definition is not None:
            result.apply_context(definition)
            from forward.strategy.strategies import paper_deployment_settings

            for name, value in paper_deployment_settings(definition).items():
                setattr(result, name, tuple(value) if name == "symbols" else value)
        return result

    @staticmethod
    def _required_string(config: Mapping[str, Any], name: str) -> str:
        """Return a required non-empty string from a saved config.

        Args:
            config: Saved deployment settings.
            name: Required setting name.

        Returns:
            The stripped setting value.

        Raises:
            ValueError: If the setting is absent, not a string, or blank.
        """
        value = config.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"deployment config {name} must be a non-empty string")
        return value.strip()

    @staticmethod
    def _required_positive_integer(config: Mapping[str, Any], name: str) -> int:
        """Return a required positive integer setting.

        Args:
            config: Saved deployment settings.
            name: Required setting name.

        Returns:
            The positive integer value.

        Raises:
            ValueError: If the setting is absent, not an integer, or not positive.
        """
        value = config.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"deployment config {name} must be a positive integer")
        return value

    @staticmethod
    def _positive_integer(
        config: Mapping[str, Any], name: str, default: int
    ) -> int:
        """Read an optional platform integer setting, validating supplied values.

        Args:
            config: Saved deployment settings.
            name: Optional setting name.
            default: Documented platform value used only when the field is absent.

        Returns:
            The supplied or platform-default positive integer.

        Raises:
            ValueError: If a supplied value is not a positive integer.
        """
        if name not in config:
            return default
        value = config[name]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"deployment config {name} must be a positive integer")
        return value

    @staticmethod
    def _positive_number(
        config: Mapping[str, Any], name: str, default: float
    ) -> float:
        """Read an optional platform number setting, validating supplied values.

        Args:
            config: Saved deployment settings.
            name: Optional setting name.
            default: Documented platform value used only when the field is absent.

        Returns:
            The supplied or platform-default positive number.

        Raises:
            ValueError: If a supplied value is not finite and positive.
        """
        if name not in config:
            return default
        value = config[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"deployment config {name} must be a finite positive number")
        try:
            number = float(value)
        except OverflowError as exc:
            raise ValueError(
                f"deployment config {name} must be a finite positive number"
            ) from exc
        if not math.isfinite(number) or number <= 0:
            raise ValueError(f"deployment config {name} must be a finite positive number")
        return number

    @staticmethod
    def _optional_positive_number(
        config: Mapping[str, Any], name: str
    ) -> float | None:
        """Read an optional positive number.

        Args:
            config: Saved deployment settings.
            name: Optional setting name.

        Returns:
            A finite positive value or ``None`` when the setting is absent.

        Raises:
            ValueError: If a supplied value is not finite and positive.
        """
        if name not in config:
            return None
        value = config[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"deployment config {name} must be a finite positive number")
        try:
            number = float(value)
        except OverflowError as exc:
            raise ValueError(
                f"deployment config {name} must be a finite positive number"
            ) from exc
        if not math.isfinite(number) or number <= 0:
            raise ValueError(f"deployment config {name} must be a finite positive number")
        return number

    @staticmethod
    def _optional_string(config: Mapping[str, Any], name: str) -> str | None:
        """Read an optional non-empty string setting.

        Args:
            config: Saved deployment settings.
            name: Optional setting name.

        Returns:
            The stripped string, or ``None`` when the field is absent.

        Raises:
            ValueError: If a supplied value is not a non-empty string.
        """
        if name not in config:
            return None
        value = config[name]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"deployment config {name} must be a non-empty string")
        return value.strip()
