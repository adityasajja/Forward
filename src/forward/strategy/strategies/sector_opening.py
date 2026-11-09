"""Pinned, cross-sectional opening selection. Execution belongs to the paper runner."""
from __future__ import annotations

import re

ENGINE = "nifty_opening_sector_momentum"
CONTEXT = {"style": "intraday", "timeframe": "1m", "hold_overnight": False,
           "entry_cutoff": "09:21", "square_off_time": "15:20"}
PAPER_SETTINGS = {
    "exchange": "NSEEQ",
    "symbol_exchanges": {},
    "max_open_positions": 1,
    "stop_loss_pct": 1,
    "take_profit_pct": 1,
}


def validate_definition(definition: dict) -> dict:
    params = definition.get("params") or {}
    universe = params.get("universe")
    if not isinstance(universe, dict) or not isinstance(universe.get("sectors"), dict):
        raise ValueError("Pin an index and sector constituent universe in params.universe")
    if any(definition.get(k) is not None for k in ("entry", "exit", "rules", "sizing")):
        raise ValueError("Opening-sector timing, ranking and sizing cannot be overridden by generic rules")
    if params.get("allocation") != 0.98 or params.get("max_positions") != 1:
        raise ValueError("Opening-sector execution uses 98% notional, a 2% cost buffer and one position")
    if any((definition.get("context") or {}).get(k) != v for k, v in CONTEXT.items()):
        raise ValueError("Opening-sector execution requires 1m intraday context, 09:21 cutoff and 15:20 square-off")
    return validate_universe(universe)


def params_schema() -> dict:
    """Return the required saved-input schema for this engine.

    Returns:
        A JSON Schema for explicit allocation, position count, and pinned
        index and constituent universe inputs.
    """
    return {
        "type": "object",
        "required": ["allocation", "max_positions", "universe"],
        "properties": {
            "allocation": {"type": "number", "exclusiveMinimum": 0},
            "max_positions": {"type": "integer", "minimum": 1},
            "universe": {
                "type": "object",
                "required": ["index", "sectors"],
                "properties": {
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
                                    "minItems": 1,
                                    "items": {"type": "string", "minLength": 1},
                                },
                            },
                            "additionalProperties": False,
                        },
                    },
                },
                "additionalProperties": False,
            },
        },
        "additionalProperties": False,
    }


def validate_universe(universe: dict) -> dict:
    """Validate a pinned index and sector universe shared by saved strategies.

    Args:
        universe: The ``params.universe`` object from a saved definition.

    Returns:
        The validated universe object.

    Raises:
        ValueError: If its index, topics, sectors, or constituents are invalid.
    """
    if not isinstance(universe, dict) or not isinstance(universe.get("sectors"), dict):
        raise ValueError("Pin an index and sector constituent universe in params.universe")
    items = [universe.get("index"), *universe["sectors"].values()]
    if not universe["sectors"] or len(items) > 40:
        raise ValueError("A nonempty bounded sector universe is required")
    if universe.get("index", {}).get("name") != "NIFTY 50":
        raise ValueError("The direction index must be NIFTY 50")
    for item in items:
        if not isinstance(item, dict) or not re.fullmatch(r"nseeq/\d+", str(item.get("topic", ""))):
            raise ValueError("Every index needs a verified NSEEQ subscription topic")
    for sector in universe["sectors"].values():
        members = sector.get("members")
        if not isinstance(members, list) or not members or len(members) > 200:
            raise ValueError("Every sector needs pinned constituents")
        if any(not isinstance(s, str) or not re.fullmatch(r"[A-Z0-9&.\-]+-EQ", s) for s in members):
            raise ValueError("Sector constituents must be NSE cash equity symbols ending -EQ")
    return universe


def symbols(universe: dict) -> tuple[str, ...]:
    return tuple(sorted({s for sector in universe["sectors"].values() for s in sector["members"]}))


class NiftyOpeningSectorMomentum:
    name = ENGINE
    supports_backtest = False
    params_schema = staticmethod(params_schema)

    @staticmethod
    def paper_deployment_settings(definition: dict) -> dict:
        """Return the runner settings required by a pinned strategy version.

        Args:
            definition: The saved strategy version.

        Returns:
            Settings that the paper runner must use for this strategy.
        """
        universe = validate_definition(definition)
        return {
            **PAPER_SETTINGS,
            "symbols": list(symbols(universe)),
            "timeframe": CONTEXT["timeframe"],
            "entry_cutoff": CONTEXT["entry_cutoff"],
            "square_off_time": CONTEXT["square_off_time"],
        }

    def __init__(self, **params):
        raise ValueError("Use the timestamped cross-sectional sector backtester; ordinary single-stock bars cannot validate this strategy")

    @staticmethod
    def signal_rules():
        from forward.signals.models import EntryRules, ExitRules
        # Never let a generic evaluator open positions for this engine.
        return (EntryRules(custom_entry_formula="0 > 1"),
                ExitRules(stop_loss_pct=1, take_profit_pct=1, trailing_stop_pct=None,
                          trend_sma=0, rsi_overbought=None, min_history_bars=0))
