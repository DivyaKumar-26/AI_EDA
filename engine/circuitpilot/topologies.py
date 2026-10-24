"""Extensible topology registration with strict, dependency-free parameter checks."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Callable, Mapping

from .ir import CircuitIR


@dataclass(frozen=True)
class TopologyDefinition:
    topology_id: str
    parameter_schema: dict
    synthesize_fn: Callable[[dict], CircuitIR]
    description: str = ""


class TopologyRegistry:
    """Register topology factories and synthesize validated canonical circuits."""

    def __init__(self) -> None:
        self._definitions: dict[str, TopologyDefinition] = {}

    def register(self, definition: TopologyDefinition) -> None:
        key = definition.topology_id.casefold()
        if not key or key in self._definitions:
            raise ValueError(f"topology id is empty or already registered: {definition.topology_id!r}")
        if definition.parameter_schema.get("type") != "object":
            raise ValueError("topology parameter schema must describe an object")
        self._definitions[key] = definition

    def ids(self) -> tuple[str, ...]:
        return tuple(definition.topology_id for definition in self._definitions.values())

    def synthesize(self, topology_id: str, parameters: Mapping[str, object]) -> CircuitIR:
        definition = self._definitions.get(topology_id.casefold())
        if definition is None:
            raise ValueError(f"unknown topology: {topology_id}")
        normalized = dict(parameters)
        _validate_object(normalized, definition.parameter_schema)
        result = definition.synthesize_fn(normalized)
        if not isinstance(result, CircuitIR):
            raise TypeError(f"topology {topology_id} did not return CircuitIR")
        return result

    def schema_document(self) -> dict:
        """Return a JSON Schema document for all currently registered designs."""
        variants = []
        for definition in self._definitions.values():
            variants.append({
                "type": "object",
                "properties": {
                    "topology": {"const": definition.topology_id},
                    "parameters": definition.parameter_schema,
                },
                "required": ["topology", "parameters"],
                "additionalProperties": False,
            })
        return {"$schema": "https://json-schema.org/draft/2020-12/schema", "title": "CircuitPilot design request",
                "oneOf": variants}


def _validate_object(values: dict, schema: dict) -> None:
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    missing = [key for key in required if key not in values]
    if missing:
        raise ValueError("missing required topology parameter(s): " + ", ".join(missing))
    extras = set(values) - set(properties)
    if extras and schema.get("additionalProperties", False) is False:
        raise ValueError("unsupported topology parameter(s): " + ", ".join(sorted(extras)))
    for key, value in values.items():
        rule = properties.get(key)
        if rule is None:
            continue
        expected = rule.get("type")
        if expected == "number":
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(float(value)):
                raise ValueError(f"topology parameter {key} must be a finite number")
            number = float(value)
        elif expected == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"topology parameter {key} must be an integer")
            number = value
        elif expected == "string":
            if not isinstance(value, str):
                raise ValueError(f"topology parameter {key} must be a string")
            number = None
        else:
            raise ValueError(f"unsupported schema type for topology parameter {key}: {expected!r}")
        if number is not None and "minimum" in rule and number < rule["minimum"]:
            raise ValueError(f"topology parameter {key} is below its minimum")
        if number is not None and "maximum" in rule and number > rule["maximum"]:
            raise ValueError(f"topology parameter {key} is above its maximum")
