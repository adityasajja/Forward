"""Adaptive strategy parameters specification and extraction.

Only parameters explicitly marked as adaptive may be optimized.
The optimizer is strictly forbidden from modifying arbitrary strategy logic.
"""

from __future__ import annotations

import copy
import math
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class AdaptiveParameter:
    """Specification for a strategy parameter that is permitted to be adapted."""

    name: str
    current: float
    minimum: float
    maximum: float
    step: float
    block: str
    adaptive: bool = True
    description: str = ""

    def validate_bounds(self) -> None:
        """Check that the parameter has finite, usable bounds."""
        values = (self.current, self.minimum, self.maximum, self.step)
        if not all(math.isfinite(value) for value in values):
            raise ValueError(f"Parameter {self.name}: values must be finite")
        if self.minimum >= self.maximum:
            raise ValueError(f"Parameter {self.name}: minimum ({self.minimum}) must be less than maximum ({self.maximum})")
        if self.step <= 0:
            raise ValueError(f"Parameter {self.name}: step ({self.step}) must be strictly positive")
        if not (self.minimum <= self.current <= self.maximum):
            raise ValueError(
                f"Parameter {self.name}: current value must be within its declared bounds"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def extract_adaptive_parameters(definition: dict[str, Any]) -> dict[str, AdaptiveParameter]:
    """Extract all parameters explicitly declared as adaptive from a strategy definition.

    Parameters must be declared as a list under `adaptive_parameters`:
    [
        {
            "name": "volume_multiple",
            "current": 1.5,
            "minimum": 1.2,
            "maximum": 3.0,
            "step": 0.1,
            "adaptive": true
        }
    ]
    Every item must declare whether it is adaptive. An enabled item must also
    declare its current value, bounds, step, and configuration block. The
    current value must match the strategy definition.

    Args:
        definition: Strategy definition containing ``adaptive_parameters``.

    Returns:
        Adaptive parameters keyed by their unique names.

    Raises:
        TypeError: If the definition or adaptive parameter list has the wrong type.
        ValueError: If an adaptive parameter is incomplete or invalid.
    """
    if not isinstance(definition, dict):
        raise TypeError("strategy definition must be an object")

    if "adaptive_parameters" not in definition:
        return {}
    raw_adaptive = definition["adaptive_parameters"]
    if not isinstance(raw_adaptive, list):
        raise TypeError("adaptive_parameters must be a list of objects")

    result: dict[str, AdaptiveParameter] = {}
    required_fields = {"name", "adaptive"}
    parameter_fields = {"current", "minimum", "maximum", "step", "block"}
    allowed_fields = required_fields | parameter_fields | {"description"}
    declared_names: set[str] = set()
    for index, item in enumerate(raw_adaptive):
        if not isinstance(item, dict):
            raise TypeError(f"adaptive_parameters[{index}] must be an object")
        unknown_fields = item.keys() - allowed_fields
        if unknown_fields:
            raise ValueError(
                f"adaptive_parameters[{index}] has unknown fields: "
                f"{', '.join(sorted(map(str, unknown_fields)))}"
            )
        missing_fields = required_fields - item.keys()
        if missing_fields:
            raise ValueError(
                f"adaptive_parameters[{index}] is missing required fields: "
                f"{', '.join(sorted(missing_fields))}"
            )

        if not isinstance(item["adaptive"], bool):
            raise TypeError(f"adaptive_parameters[{index}].adaptive must be a boolean")
        name = item["name"]
        if not isinstance(name, str) or not name:
            raise ValueError(f"adaptive_parameters[{index}].name must be a non-empty string")
        if name in declared_names:
            raise ValueError(f"adaptive parameter {name!r} is declared more than once")
        declared_names.add(name)
        if not item["adaptive"]:
            continue

        missing_parameter_fields = parameter_fields - item.keys()
        if missing_parameter_fields:
            raise ValueError(
                f"adaptive_parameters[{index}] is missing required fields: "
                f"{', '.join(sorted(missing_parameter_fields))}"
            )
        block = item["block"]
        if not isinstance(block, str) or block not in {"entry", "exit", "params"}:
            raise ValueError(
                f"adaptive_parameters[{index}].block must be entry, exit, or params"
            )
        numeric_fields = ("current", "minimum", "maximum", "step")
        for field_name in numeric_fields:
            value = item[field_name]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"adaptive_parameters[{index}].{field_name} must be a number")

        if block == "params":
            values = definition.get("params")
        elif "rules" in definition:
            rule_blocks_value = definition["rules"]
            if not isinstance(rule_blocks_value, dict):
                raise TypeError("strategy rules must be an object")
            values = rule_blocks_value.get(block)
        else:
            values = definition.get(block)
        if not isinstance(values, dict) or name not in values:
            raise ValueError(
                f"adaptive parameter {name!r} is missing from its {block!r} block"
            )
        configured_value = values[name]
        if isinstance(configured_value, bool) or not isinstance(configured_value, (int, float)):
            raise TypeError(f"adaptive parameter {name!r} must have a numeric configured value")
        if "description" in item and not isinstance(item["description"], str):
            raise TypeError(f"adaptive_parameters[{index}].description must be a string")
        current = float(item["current"])
        if float(configured_value) != current:
            raise ValueError(
                f"adaptive parameter {name!r} current value does not match its {block!r} block"
            )
        param = AdaptiveParameter(
            name=name,
            current=current,
            minimum=float(item["minimum"]),
            maximum=float(item["maximum"]),
            step=float(item["step"]),
            block=block,
            adaptive=True,
            description=str(item.get("description", "")),
        )
        param.validate_bounds()
        result[name] = param

    return result


def locate_parameter(
    definition: dict[str, Any], parameter_name: str, block: str | None = None
) -> tuple[str, dict[str, Any]]:
    """Find one existing strategy parameter in its declared block.

    Args:
        definition: Strategy definition that owns the parameter.
        parameter_name: Existing parameter key.
        block: Required block when the name is ambiguous. Adaptive declarations
            supply their own block.

    Returns:
        The selected block and its mutable value mapping.

    Raises:
        TypeError: If the definition or a strategy block has the wrong type.
        ValueError: If the parameter is missing, ambiguous, or in the wrong block.
    """
    if not isinstance(definition, dict):
        raise TypeError("strategy definition must be an object")

    containers: dict[str, dict[str, Any]] = {}
    if "rules" in definition:
        rules = definition["rules"]
        if not isinstance(rules, dict):
            raise TypeError("strategy rules must be an object")
        for target in ("entry", "exit"):
            if target in rules:
                values = rules[target]
                if not isinstance(values, dict):
                    raise TypeError(f"strategy rules.{target} must be an object")
                containers[target] = values
    else:
        for target in ("entry", "exit"):
            if target in definition:
                values = definition[target]
                if not isinstance(values, dict):
                    raise TypeError(f"strategy {target} must be an object")
                containers[target] = values
    if "params" in definition:
        params = definition["params"]
        if not isinstance(params, dict):
            raise TypeError("strategy params must be an object")
        containers["params"] = params

    adaptive_spec = extract_adaptive_parameters(definition).get(parameter_name)
    if block is None:
        if adaptive_spec is not None:
            target_block = adaptive_spec.block
        else:
            matches = [key for key, values in containers.items() if parameter_name in values]
            if len(matches) != 1:
                raise ValueError(
                    f"parameter {parameter_name!r} must exist in exactly one block"
                )
            target_block = matches[0]
    else:
        if not isinstance(block, str):
            raise TypeError("block must be a string")
        if adaptive_spec is not None and block != adaptive_spec.block:
            raise ValueError(
                f"adaptive parameter {parameter_name!r} belongs to "
                f"the {adaptive_spec.block!r} block"
            )
        target_block = block

    if target_block not in containers or parameter_name not in containers[target_block]:
        raise ValueError(
            f"parameter {parameter_name!r} is not present in the {target_block!r} block"
        )
    return target_block, containers[target_block]


def apply_parameter_to_definition(
    definition: dict[str, Any],
    parameter_name: str,
    new_value: float,
    block: str | None = None,
) -> dict[str, Any]:
    """Return a new deep-copied strategy definition with the parameter updated.

    Keeps the baseline structure intact and updates an existing parameter in
    its named block. Without a block, the parameter must appear in one block only.
    It also updates the declared current value for an adaptive parameter.

    Args:
        definition: Strategy definition to copy and update.
        parameter_name: Existing parameter key to update.
        new_value: Finite value written to the definition.
        block: Exact configuration block, or ``None`` when the parameter is
            declared in exactly one block or its adaptive declaration names it.

    Returns:
        A deep copy of the definition with the parameter value updated.

    Raises:
        TypeError: If a strategy block or value has an invalid type.
        ValueError: If the parameter is missing, ambiguous, or not finite.
    """
    if isinstance(new_value, bool) or not isinstance(new_value, (int, float)):
        raise TypeError("new_value must be a number")
    if not math.isfinite(new_value):
        raise ValueError("new_value must be finite")
    if not isinstance(definition, dict):
        raise TypeError("strategy definition must be an object")
    updated = copy.deepcopy(definition)
    adaptive_specs = extract_adaptive_parameters(updated)
    adaptive_spec = adaptive_specs.get(parameter_name)
    if adaptive_spec and not adaptive_spec.minimum <= new_value <= adaptive_spec.maximum:
        raise ValueError(
            f"parameter {parameter_name!r} must stay within its declared adaptive bounds"
        )
    target_block, container = locate_parameter(updated, parameter_name, block)

    # Round to reasonable precision based on step/float
    rounded_val: float | int = round(float(new_value), 4)
    if rounded_val.is_integer():
        rounded_val = int(rounded_val)

    container[parameter_name] = rounded_val

    # Update adaptive_parameters entry if present
    if "adaptive_parameters" in updated:
        raw_ad = updated["adaptive_parameters"]
        if not isinstance(raw_ad, list):
            raise TypeError("adaptive_parameters must be a list of objects")
        matching = [
            item for item in raw_ad
            if isinstance(item, dict) and item.get("name") == parameter_name
        ]
        if len(matching) > 1:
            raise ValueError(f"adaptive parameter {parameter_name!r} is declared more than once")
        if matching and parameter_name in adaptive_specs:
            matching[0]["current"] = rounded_val

    return updated
