"""Built-in strategies."""

from typing import Any, get_type_hints

from forward.signals.cross_sectional import CrossSectionalMomentum
from forward.signals.strategy import SignalEntryStrategy
from forward.strategy.strategies.alpha_candidates import (
    ShortHorizonReversal,
    TrendFilteredExposure,
    VolatilityManaged,
)
from forward.strategy.strategies.episodic_pivot import (
    EpisodicPivot9M,
    EpisodicPivotDay1,
    EpisodicPivotDelayed,
)
from forward.strategy.strategies.momentum_breakout import MomentumBreakout
from forward.strategy.strategies.orb import OpeningRangeBreakout
from forward.strategy.strategies.oversold_reversion import ValidatedOversoldReversion
from forward.strategy.strategies.paper_alpha import (
    AvellanedaLeeMeanReversion,
    IimaNseMomentum,
    JegadeeshTitmanMomentum,
    MultiFactorComposite,
    Nism52wHighProximity,
    SehgalLowVolAnomaly,
    VolatilityBreakout,
)
from forward.strategy.strategies.python_rules import PythonRules
from forward.strategy.strategies.regime_adaptive import RegimeAdaptiveStrategy
from forward.strategy.strategies.sector_opening import NiftyOpeningSectorMomentum
from forward.strategy.strategies.sma_crossover import SmaCrossover
from forward.strategy.strategies.vwap_reversion import IntradayVwapMeanReversion

STRATEGIES = {
    IntradayVwapMeanReversion.name: IntradayVwapMeanReversion,
    NiftyOpeningSectorMomentum.name: NiftyOpeningSectorMomentum,
    PythonRules.name: PythonRules,
    ValidatedOversoldReversion.name: ValidatedOversoldReversion,
    RegimeAdaptiveStrategy.name: RegimeAdaptiveStrategy,
    SmaCrossover.name: SmaCrossover,
    MomentumBreakout.name: MomentumBreakout,
    OpeningRangeBreakout.name: OpeningRangeBreakout,
    SignalEntryStrategy.name: SignalEntryStrategy,
    CrossSectionalMomentum.name: CrossSectionalMomentum,
    # Academic paper models. Registered so the walk-forward harness can score
    # them; their live-signal confidence numbers are assumptions until a
    # validation run says otherwise.
    JegadeeshTitmanMomentum.name: JegadeeshTitmanMomentum,
    AvellanedaLeeMeanReversion.name: AvellanedaLeeMeanReversion,
    VolatilityBreakout.name: VolatilityBreakout,
    MultiFactorComposite.name: MultiFactorComposite,
    IimaNseMomentum.name: IimaNseMomentum,
    Nism52wHighProximity.name: Nism52wHighProximity,
    SehgalLowVolAnomaly.name: SehgalLowVolAnomaly,
    # Pradeep Bonde's Episodic Pivot family (see episodic_pivot.py for what
    # survives translation from a news-driven discretionary model to daily bars).
    EpisodicPivotDay1.name: EpisodicPivotDay1,
    EpisodicPivotDelayed.name: EpisodicPivotDelayed,
    EpisodicPivot9M.name: EpisodicPivot9M,
    # Pre-registered candidates for the alpha hunt — see alpha_candidates.py for
    # the prior behind each and the condition that falsifies it.
    ShortHorizonReversal.name: ShortHorizonReversal,
    VolatilityManaged.name: VolatilityManaged,
    TrendFilteredExposure.name: TrendFilteredExposure,
}


def paper_deployment_settings(definition: dict[str, Any]) -> dict[str, Any]:
    """Return strategy-owned settings for a saved paper deployment.

    Args:
        definition: The saved strategy version.

    Returns:
        Strategy-specific runner settings, or an empty mapping when the
        strategy uses only its saved deployment config and context.
    """
    engine_key = definition.get("engine_key")
    strategy = STRATEGIES.get(engine_key)
    settings_for = getattr(strategy, "paper_deployment_settings", None)
    return settings_for(definition) if settings_for else {}


def engine_parameter_schemas() -> list[dict[str, Any]]:
    """Return saved-parameter schemas declared by registered strategy engines.

    Returns:
        Engine keys and their declared JSON-compatible parameter schemas.
        Engines without an authoring schema are reported with ``None`` rather
        than a guessed or default schema.
    """
    schemas = []
    for engine_key, strategy in STRATEGIES.items():
        schema_provider = getattr(strategy, "params_schema", None)
        schema = schema_provider() if schema_provider else _annotation_schema(strategy)
        schemas.append(
            {
                "engine_key": engine_key,
                "params_schema": schema,
            }
        )
    return schemas


def _annotation_schema(strategy: type) -> dict[str, Any]:
    """Build a required JSON Schema from a strategy's declared parameters.

    Class annotations are the source of truth for the engine's parameter names
    and types. Class attribute values are deliberately not copied into the
    authoring schema, so the UI cannot silently prefill strategy behavior.

    Args:
        strategy: Registered strategy class.

    Returns:
        A schema requiring every declared runtime parameter.
    """
    annotations: dict[str, Any] = {}
    for base in reversed(strategy.__mro__):
        annotations.update(get_type_hints(base))
    annotations.pop("name", None)
    annotations.pop("paper_id", None)
    properties: dict[str, dict[str, Any]] = {}
    for name, annotation in annotations.items():
        if name.startswith("_"):
            continue
        if annotation is bool:
            value_type = "boolean"
        elif annotation is int:
            value_type = "integer"
        elif annotation is float:
            value_type = "number"
        elif annotation is str:
            value_type = "string"
        else:
            value_type = "string"
        properties[name] = {
            "type": value_type,
            "title": name.replace("_", " ").capitalize(),
            "description": f"Required parameter for {strategy.name}.",
        }
    if strategy.name == PythonRules.name:
        properties["source"] = {
            "type": "string",
            "title": "Rule source",
            "minLength": 1,
            "description": "Required Python rule source for this engine.",
        }
    return {
        "type": "object",
        "required": list(properties),
        "properties": properties,
        "additionalProperties": False,
    }

__all__ = [
    "RegimeAdaptiveStrategy",
    "SmaCrossover",
    "OpeningRangeBreakout",
    "SignalEntryStrategy",
    "CrossSectionalMomentum",
    "JegadeeshTitmanMomentum",
    "AvellanedaLeeMeanReversion",
    "VolatilityBreakout",
    "MultiFactorComposite",
    "IimaNseMomentum",
    "Nism52wHighProximity",
    "SehgalLowVolAnomaly",
    "EpisodicPivotDay1",
    "EpisodicPivotDelayed",
    "EpisodicPivot9M",
    "ShortHorizonReversal",
    "VolatilityManaged",
    "TrendFilteredExposure",
    "STRATEGIES",
    "paper_deployment_settings",
    "engine_parameter_schemas",
]
