"""Validation and fail-closed evaluation for supported design specifications."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from .designs import EXAMPLE_DESIGNS


_COMMON_AMPLIFIER = {
    "gain_at_1khz_magnitude_v_per_v": "V/V",
    "gain_at_1khz_db": "dB",
    "peak_gain_magnitude_v_per_v": "V/V",
    "peak_gain_db": "dB",
    "phase_at_1khz_degrees": "deg",
    "minus_3db_low_cutoff_hz": "Hz",
    "minus_3db_high_cutoff_hz": "Hz",
    "bandwidth_hz": "Hz",
}
_TOPOLOGY_METRICS = {
    "mosfet_amplifier": {"bias.vgs_v": "V", "bias.vds_v": "V", "bias.drain_current_a_estimate": "A"},
    "bjt_amplifier": {"bias.vbe_v": "V", "bias.vce_v": "V", "bias.collector_current_a_estimate": "A"},
    "mosfet_bias": {"nominal_bias.vgs_v": "V", "nominal_bias.vds_v": "V",
                     "nominal_bias.drain_current_a_estimate": "A", "nominal_supply_v": "V",
                     "sweep_points": "count", "sweep_summary.active_region_points": "count",
                     "sweep_summary.total_points": "count", "sweep_summary.min_current_a": "A",
                     "sweep_summary.max_current_a": "A"},
    "bjt_bias": {"nominal_bias.vbe_v": "V", "nominal_bias.vce_v": "V",
                  "nominal_bias.collector_current_a_estimate": "A", "nominal_supply_v": "V",
                  "sweep_points": "count", "sweep_summary.active_region_points": "count",
                  "sweep_summary.total_points": "count", "sweep_summary.min_current_a": "A",
                  "sweep_summary.max_current_a": "A"},
}


def _available_metrics(design_id: str) -> dict[str, str]:
    design = EXAMPLE_DESIGNS[design_id]
    if design.measurement_kind.endswith("amplifier"):
        return {**_COMMON_AMPLIFIER, **_TOPOLOGY_METRICS.get(design.measurement_kind, {})}
    return _TOPOLOGY_METRICS.get(design.measurement_kind, {})


def validate_spec(spec: Any) -> dict:
    """Validate a v1 machine-readable spec. Returns a detached normalized mapping."""
    if not isinstance(spec, dict):
        raise ValueError("specification must be a JSON object")
    _keys(spec, {"schema_version", "design_id", "operating_conditions", "requirements"}, "specification")
    if spec.get("schema_version") != 1 or isinstance(spec.get("schema_version"), bool):
        raise ValueError("schema_version must be 1")
    design_id = spec.get("design_id")
    if not isinstance(design_id, str) or design_id not in EXAMPLE_DESIGNS:
        raise ValueError(f"unknown design_id: {design_id!r}")
    conditions = spec.get("operating_conditions")
    if not isinstance(conditions, dict):
        raise ValueError("operating_conditions must be an object")
    required_conditions = {"supply_mode", "nominal_supply_v", "supply_tolerance_percent", "ambient_temperature_c",
                           "resistor_tolerance_percent", "capacitor_tolerance_percent", "load_resistance_ohm",
                           "source_resistance_ohm", "input_amplitude_v", "input_frequency_hz",
                           "maximum_output_peak_v", "maximum_thd_percent"}
    _keys(conditions, required_conditions, "operating_conditions")
    if not isinstance(conditions["supply_mode"], str) or conditions["supply_mode"] not in {"single", "dual"}:
        raise ValueError("supply_mode must be 'single' or 'dual'")
    _number(conditions["nominal_supply_v"], "nominal_supply_v", minimum=0, exclusive_min=True)
    for key in ("supply_tolerance_percent", "resistor_tolerance_percent", "capacitor_tolerance_percent"):
        _number(conditions[key], key, minimum=0, maximum=100, exclusive_max=True)
    temperatures = conditions["ambient_temperature_c"]
    if not isinstance(temperatures, list) or len(temperatures) != 3:
        raise ValueError("ambient_temperature_c must contain exactly three points")
    for temp in temperatures:
        _number(temp, "ambient_temperature_c item", minimum=0, maximum=70)
    if temperatures != sorted(temperatures):
        raise ValueError("ambient_temperature_c must be ordered from low to high")
    for key in ("load_resistance_ohm", "source_resistance_ohm", "input_amplitude_v", "input_frequency_hz", "maximum_output_peak_v"):
        _nullable_number(conditions[key], key, minimum=0, exclusive_min=True)
    _nullable_number(conditions["maximum_thd_percent"], "maximum_thd_percent", minimum=0, maximum=100)

    requirements = spec.get("requirements")
    if not isinstance(requirements, list) or not requirements:
        raise ValueError("requirements must be a non-empty array")
    metrics = _available_metrics(design_id)
    seen = set()
    normalized = []
    for index, requirement in enumerate(requirements):
        label = f"requirements[{index}]"
        if not isinstance(requirement, dict):
            raise ValueError(f"{label} must be an object")
        _keys(requirement, {"metric", "unit", "min", "max"}, label)
        metric, unit = requirement.get("metric"), requirement.get("unit")
        if not isinstance(metric, str) or metric not in metrics:
            raise ValueError(f"unsupported metric for {design_id}: {metric!r}")
        if metric in seen:
            raise ValueError(f"duplicate requirement metric: {metric}")
        seen.add(metric)
        if unit != metrics[metric]:
            raise ValueError(f"metric {metric} requires unit {metrics[metric]!r}")
        low = _number(requirement.get("min"), f"{label}.min")
        high = _number(requirement.get("max"), f"{label}.max")
        if low > high:
            raise ValueError(f"{label}.min must be less than or equal to max")
        normalized.append({"metric": metric, "unit": unit, "min": low, "max": high})
    return {"schema_version": 1, "design_id": design_id, "operating_conditions": dict(conditions), "requirements": normalized}


def load_spec(path: str | Path) -> dict:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read valid JSON specification: {exc}") from exc
    return validate_spec(raw)


def evaluate_requirements(spec: dict, measurements: dict) -> dict:
    """Evaluate inclusive bounds. Missing and non-finite values fail closed."""
    spec = validate_spec(spec)
    checks = []
    for requirement in spec["requirements"]:
        observed: Any = measurements
        for key in requirement["metric"].split("."):
            observed = observed.get(key) if isinstance(observed, dict) else None
        valid = isinstance(observed, (int, float)) and not isinstance(observed, bool) and math.isfinite(observed)
        passed = valid and requirement["min"] <= observed <= requirement["max"]
        checks.append({**requirement, "observed": observed if valid else None, "pass": bool(passed),
                       "failure": None if passed else ("measurement missing or non-finite" if not valid else "outside inclusive bounds")})
    return {"status": "passed" if all(item["pass"] for item in checks) else "failed", "checks": checks}


def _keys(value: dict, required: set[str], label: str) -> None:
    missing = required - value.keys()
    extra = value.keys() - required
    if missing:
        raise ValueError(f"{label} missing field(s): {', '.join(sorted(missing))}")
    if extra:
        raise ValueError(f"{label} has unsupported field(s): {', '.join(sorted(extra))}")


def _number(value: Any, label: str, minimum: float | None = None, maximum: float | None = None,
            exclusive_min: bool = False, exclusive_max: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    if minimum is not None and (value <= minimum if exclusive_min else value < minimum):
        raise ValueError(f"{label} must be {'greater than' if exclusive_min else 'at least'} {minimum}")
    if maximum is not None and (value >= maximum if exclusive_max else value > maximum):
        raise ValueError(f"{label} must be {'less than' if exclusive_max else 'at most'} {maximum}")
    return float(value)


def _nullable_number(value: Any, label: str, minimum: float | None = None, maximum: float | None = None,
                     exclusive_min: bool = False, exclusive_max: bool = False) -> float | None:
    return None if value is None else _number(value, label, minimum, maximum, exclusive_min, exclusive_max)
