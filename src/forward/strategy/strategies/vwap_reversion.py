"""Paper-only VWAP reversion engine and its saved-parameter contract."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

ENGINE = "intraday_vwap_mean_reversion"

_VWAP_PARAMETER_TYPES = {
    "session_start": str,
    "entry_start": str,
    "entry_end": str,
    "square_off_time": str,
    "max_open_positions": int,
    "bar_minutes": int,
    "warmup_bars": int,
    "vwap_slope_bars": int,
    "vwap_residual_window": int,
    "atr_period": int,
    "index_move_lookback_minutes": int,
    "entry_z_score": float,
    "min_vwap_discount_pct": float,
    "min_atr_pct": float,
    "max_vwap_slope_pct": float,
    "max_index_move_pct": float,
    "max_quote_age_seconds": float,
    "max_signal_age_seconds": float,
    "max_spread_pct": float,
    "min_average_value": float,
    "previous_close_gap_limit_pct": float,
    "max_adv_participation": float,
    "max_single_name_equity_pct": float,
    "max_book_exposure_pct": float,
    "max_planned_loss_pct": float,
    "max_position_loss_pct": float,
    "entry_price_pad_pct": float,
    "entry_limit_pad_pct": float,
    "exit_price_pad_pct": float,
    "target_exit_factor": float,
    "min_net_gain_pct": float,
    "min_reward_risk": float,
    "impact_scale_bps": float,
    "impact_multiplier": float,
    "cash_reserve_pct": float,
    "max_daily_loss_pct": float,
    "max_entry_attempts": int,
    "max_hold_minutes": int,
}


def params_schema() -> dict[str, Any]:
    """Return the generic strategy builder schema for required VWAP parameters.

    Returns:
        A JSON-compatible schema. It contains field types and labels but no
        behavioral defaults; the author must supply every value in the saved
        strategy version.
    """
    required = list(_VWAP_PARAMETER_TYPES)
    properties: dict[str, dict[str, Any]] = {}
    for name, value_type in _VWAP_PARAMETER_TYPES.items():
        field_schema: dict[str, Any] = {
            "type": "string" if value_type is str else "integer" if value_type is int else "number",
            "title": name.replace("_", " ").capitalize(),
            "description": f"Required strategy value for {name.replace('_', ' ')}.",
        }
        if value_type is str:
            field_schema["minLength"] = 1
            if name in {"session_start", "entry_start", "entry_end", "square_off_time"}:
                field_schema["pattern"] = r"^([01]\d|2[0-3]):[0-5]\d$"
                field_schema["description"] = "Required session time in HH:MM format."
        else:
            field_schema["exclusiveMinimum"] = 0
        properties[name] = field_schema
    return {
        "type": "object",
        "required": ["spec_version", "bar_source", "universe", "vwap"],
        "properties": {
            "spec_version": {"const": 1},
            "bar_source": {"const": "observed_live_quotes"},
            "universe": {
                "type": "object",
                "required": ["index", "sectors"],
                "properties": {
                    "as_of": {"type": "string", "format": "date"},
                    "index": {
                        "type": "object",
                        "required": ["name", "topic"],
                        "properties": {
                            "name": {"type": "string", "minLength": 1},
                            "topic": {"type": "string", "minLength": 1},
                        },
                        "additionalProperties": False,
                    },
                    "sectors": {
                        "type": "object",
                        "minProperties": 1,
                        "additionalProperties": {
                            "type": "object",
                            "required": ["topic", "members"],
                            "properties": {
                                "topic": {"type": "string", "minLength": 1},
                                "members": {
                                    "type": "array",
                                    "items": {"type": "string", "minLength": 1},
                                    "minItems": 1,
                                },
                                "source": {"type": "string", "format": "uri"},
                                "sha256": {"type": "string"},
                            },
                            "additionalProperties": False,
                        },
                    },
                },
                "additionalProperties": False,
            },
            "vwap": {
                "type": "object",
                "required": required,
                "properties": properties,
                "additionalProperties": False,
            }
        },
        "additionalProperties": False,
    }


def validate_definition(definition: dict[str, Any]) -> dict[str, Any]:
    """Validate the saved VWAP strategy parameters and return its universe.

    Args:
        definition: Saved strategy version.

    Returns:
        The checked universe definition.

    Raises:
        ValueError: If strategy rules or any required parameter is missing or
            invalid. Old fixed-rule definitions must be versioned again with
            the required parameter object.
    """
    if any(definition.get(key) is not None for key in ("entry", "exit", "rules", "sizing")):
        raise ValueError("VWAP rules belong in the saved engine parameters")
    params = definition.get("params")
    if not isinstance(params, dict):
        raise ValueError("VWAP needs saved params with a universe and vwap settings")
    if params.get("spec_version") != 1 or params.get("bar_source") != "observed_live_quotes":
        raise ValueError("Pin the supported VWAP spec_version and bar_source")
    vwap = params.get("vwap")
    if not isinstance(vwap, dict):
        raise ValueError("This VWAP version needs params.vwap; create a new version with all required settings")
    required = set(_VWAP_PARAMETER_TYPES)
    missing = sorted(required - set(vwap))
    unknown = sorted(set(vwap) - required)
    if missing or unknown:
        raise ValueError(f"invalid params.vwap; missing={missing}, unknown={unknown}")
    for name, value_type in _VWAP_PARAMETER_TYPES.items():
        value = vwap[name]
        if value_type is str:
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"params.vwap.{name} must be a non-empty string")
            if name in {"session_start", "entry_start", "entry_end", "square_off_time"}:
                try:
                    datetime.strptime(value, "%H:%M")
                except ValueError as exc:
                    raise ValueError(f"params.vwap.{name} must use HH:MM") from exc
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"params.vwap.{name} must be numeric")
        if value_type is int and not isinstance(value, int):
            raise ValueError(f"params.vwap.{name} must be an integer")
        if not math.isfinite(float(value)) or value <= 0:
            raise ValueError(f"params.vwap.{name} must be finite and positive")
    if not (vwap["session_start"] < vwap["entry_start"] < vwap["entry_end"] < vwap["square_off_time"]):
        raise ValueError("VWAP session times must be ordered from start through square-off")

    universe = params.get("universe")
    if not isinstance(universe, dict) or not isinstance(universe.get("sectors"), dict):
        raise ValueError("Pin a sector constituent universe in params.universe")
    from forward.strategy.strategies.sector_opening import validate_universe

    return validate_universe(universe)


class IntradayVwapMeanReversion:
    """Registered strategy descriptor for the paper-only VWAP engine."""

    name = ENGINE
    supports_backtest = False
    params_schema = staticmethod(params_schema)

    @staticmethod
    def paper_deployment_settings(definition: dict[str, Any]) -> dict[str, Any]:
        """Return the saved VWAP values to the runner without defaults.

        Args:
            definition: Saved strategy version.

        Returns:
            Strategy-owned runner settings from the saved version.
        """
        universe = validate_definition(definition)
        from forward.strategy.strategies.sector_opening import symbols

        params = definition["params"]["vwap"]
        return {
            "symbols": list(symbols(universe)),
            "exchange": "NSEEQ",
            "symbol_exchanges": {},
            "timeframe": f"{params['bar_minutes']}m",
            "entry_cutoff": params["entry_end"],
            "square_off_time": params["square_off_time"],
            "max_open_positions": params["max_open_positions"],
            "vwap_parameters": dict(params),
        }

    def __init__(self, **params: Any) -> None:
        """Refuse the ordinary bar runner, which cannot execute this lifecycle."""
        raise ValueError(
            "Use quote-aware VWAP replay; ordinary bar adapters cannot execute this lifecycle"
        )

    @staticmethod
    def signal_rules() -> tuple[Any, Any]:
        """Return rules that prevent generic evaluation from opening positions."""
        from forward.signals.models import EntryRules, ExitRules

        return (
            EntryRules(custom_entry_formula="0 > 1"),
            ExitRules(
                stop_loss_pct=1,
                take_profit_pct=1,
                trailing_stop_pct=None,
                trend_sma=0,
                rsi_overbought=None,
                min_history_bars=0,
            ),
        )
